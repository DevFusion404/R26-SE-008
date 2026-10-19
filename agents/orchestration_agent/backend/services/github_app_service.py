"""
GitHub App authentication
=========================
R26-SE-008 | Bandara S M Y M | IT22277886

DIWO acts on GitHub as a GitHub App, never through a personal access token:

    App private key ──RS256──> App JWT (<= 10 min)
    App JWT ──POST /app/installations/{id}/access_tokens──> installation token (1 h)
    installation token ──> REST calls scoped to that installation's repositories

Installation tokens are cached in process memory only, keyed by installation,
and refreshed five minutes before GitHub expires them. They are never logged,
persisted, or included in an exception message.

Every request has a timeout. Failures are raised as GitHubAPIError carrying
the status and whether trying again later could succeed, which is what the job
worker uses to choose between retrying and failing a job.
"""

import logging
import threading
import time
from datetime import datetime, timezone

import jwt
import requests

import config

logger = logging.getLogger("diwo.github.app")

DEFAULT_TIMEOUT = 15

#: GitHub rejects an App JWT whose lifetime exceeds 10 minutes. iat is
#: backdated 60 s to tolerate clock drift between this host and GitHub.
JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60

#: Refresh an installation token this long before it expires, so a request is
#: never made with a token that expires mid-flight.
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60


class GitHubConfigError(RuntimeError):
    """The integration is enabled but not configured well enough to run."""


class GitHubAPIError(RuntimeError):
    """A GitHub REST call failed.

    `retryable` is True for failures that may clear on their own: network
    errors, timeouts, 5xx, and rate limiting.
    """

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


def _is_rate_limited(response) -> bool:
    if response.status_code == 429:
        return True
    return (response.status_code == 403
            and (response.headers.get("X-RateLimit-Remaining") == "0"
                 or "rate limit" in (response.text or "").lower()))


def _error_from_response(response, what: str) -> GitHubAPIError:
    """A GitHubAPIError naming GitHub's own message, never request headers."""
    try:
        detail = (response.json() or {}).get("message") or ""
    except ValueError:
        detail = ""
    status = response.status_code
    retryable = status >= 500 or _is_rate_limited(response)
    message = f"{what} failed: HTTP {status}"
    if detail:
        message += f" - {detail[:300]}"
    return GitHubAPIError(message, status=status, retryable=retryable)


class GitHubAppService:
    """Authenticates as one GitHub App and calls the REST API for it."""

    def __init__(self, app_id: str, private_key: str, *,
                 api_url: str = config.GITHUB_API_URL,
                 api_version: str | None = None,
                 session: requests.Session | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        if not app_id:
            raise GitHubConfigError("GITHUB_APP_ID is not set.")
        if not private_key:
            raise GitHubConfigError(
                "No GitHub App private key: set GITHUB_PRIVATE_KEY_PEM or GITHUB_PRIVATE_KEY_PATH.")
        self._app_id = str(app_id)
        self._private_key = private_key
        self._api_url = api_url.rstrip("/")
        self._api_version = api_version or config.github_api_version()
        self._session = session or requests.Session()
        self._timeout = timeout
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def __repr__(self):
        # Never let a debug print or a traceback's locals show the key.
        return f"<GitHubAppService app_id={self._app_id}>"

    @classmethod
    def from_config(cls, **kwargs) -> "GitHubAppService":
        problems = config.github_config_problems()
        if problems:
            raise GitHubConfigError(" ".join(problems))
        return cls(config.github_app_id(), config.github_private_key(), **kwargs)

    # ── Authentication ───────────────────────────────────────────────────────

    def generate_jwt(self, now: float | None = None) -> str:
        """An RS256 App JWT valid for nine minutes."""
        now = int(now if now is not None else time.time())
        payload = {
            "iat": now - JWT_BACKDATE_SECONDS,
            "exp": now + JWT_LIFETIME_SECONDS,
            "iss": self._app_id,
        }
        try:
            return jwt.encode(payload, self._private_key, algorithm="RS256")
        except Exception:  # noqa: BLE001 - ValueError, PyJWTError, UnsupportedAlgorithm...
            # The exception text from the crypto layer can echo key material.
            raise GitHubConfigError(
                "The GitHub App private key could not be used to sign a JWT. "
                "Check that it is the PEM downloaded from the App settings."
            ) from None

    def _headers(self, token: str, bearer: bool = True) -> dict:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"{'Bearer' if bearer else 'token'} {token}",
            "X-GitHub-Api-Version": self._api_version,
            "User-Agent": "RefactorIQ-DIWO",
        }

    def _send(self, method: str, url: str, headers: dict, json=None, what: str = "GitHub request",
              **extra):
        try:
            return self._session.request(method, url, headers=headers, json=json,
                                         timeout=self._timeout, **extra)
        except requests.Timeout:
            raise GitHubAPIError(f"{what} timed out", retryable=True) from None
        except requests.RequestException as exc:
            raise GitHubAPIError(f"{what} failed: {type(exc).__name__}",
                                 retryable=True) from None

    def get_installation_token(self, installation_id: int, force_refresh: bool = False) -> str:
        """A cached installation token, exchanged for a new one when near expiry."""
        installation_id = int(installation_id)
        with self._lock:
            cached = self._tokens.get(installation_id)
            if (cached and not force_refresh
                    and cached[1] - TOKEN_REFRESH_MARGIN_SECONDS > time.time()):
                return cached[0]

            what = "Installation token exchange"
            response = self._send(
                "POST", f"{self._api_url}/app/installations/{installation_id}/access_tokens",
                headers=self._headers(self.generate_jwt()), what=what)
            if response.status_code != 201:
                raise _error_from_response(response, what)

            body = response.json()
            token = body.get("token")
            if not token:
                raise GitHubAPIError(f"{what} returned no token", status=response.status_code)
            self._tokens[installation_id] = (token, _expiry_epoch(body.get("expires_at")))
            logger.info("Obtained installation token for installation %s", installation_id)
            return token

    def invalidate_token(self, installation_id: int) -> None:
        with self._lock:
            self._tokens.pop(int(installation_id), None)

    # ── REST ─────────────────────────────────────────────────────────────────

    def request(self, method: str, path: str, installation_id: int,
                json=None, expected=(200, 201), what: str | None = None):
        """An authenticated REST call on behalf of one installation.

        A 401 is retried once with a freshly exchanged token, which covers a
        token revoked or expired early on GitHub's side. Returns parsed JSON
        (or None for an empty body).
        """
        what = what or f"GitHub {method} {path}"
        url = f"{self._api_url}/{path.lstrip('/')}"
        for attempt in (1, 2):
            token = self.get_installation_token(installation_id, force_refresh=attempt == 2)
            response = self._send(method, url, headers=self._headers(token), json=json, what=what)
            if response.status_code == 401 and attempt == 1:
                self.invalidate_token(installation_id)
                continue
            break

        if response.status_code not in expected:
            raise _error_from_response(response, what)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise GitHubAPIError(f"{what} returned a non-JSON body",
                                 status=response.status_code) from None


    def download_to_file(self, path: str, installation_id: int, destination, max_bytes: int,
                         what: str = "GitHub download") -> int:
        """Stream a binary REST response (e.g. a zipball) into `destination`.

        GitHub answers archive requests with a redirect to codeload.github.com
        carrying its own short-lived token; requests drops our Authorization
        header on that cross-host hop, and the redirect URL is never logged.
        Raises GitHubAPIError (not retryable) when the body exceeds max_bytes.
        Returns the number of bytes written.
        """
        url = f"{self._api_url}/{path.lstrip('/')}"
        headers = self._headers(self.get_installation_token(installation_id))
        headers["Accept"] = "application/vnd.github+json"
        response = self._send("GET", url, headers=headers, what=what,
                              stream=True, allow_redirects=True)
        try:
            if response.status_code == 401:
                self.invalidate_token(installation_id)
            if response.status_code != 200:
                raise _error_from_response(response, what)
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise GitHubAPIError(f"{what} exceeds the {max_bytes // (1024 * 1024)} MB limit")
            written = 0
            with open(destination, "wb") as handle:
                try:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise GitHubAPIError(
                                f"{what} exceeds the {max_bytes // (1024 * 1024)} MB limit")
                        handle.write(chunk)
                except requests.RequestException:
                    raise GitHubAPIError(f"{what} was interrupted", retryable=True) from None
            return written
        finally:
            response.close()


def _expiry_epoch(expires_at: str | None) -> float:
    """GitHub's expires_at ("2026-10-17T10:00:00Z") as epoch seconds.

    Falls back to 55 minutes from now when absent or unparseable — tokens live
    one hour, and assuming slightly less is the safe direction to be wrong in.
    """
    if expires_at:
        try:
            return datetime.fromisoformat(expires_at.replace("Z", "+00:00")) \
                .astimezone(timezone.utc).timestamp()
        except ValueError:
            pass
    return time.time() + 55 * 60
