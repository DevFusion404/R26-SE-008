"""
services/github_app_service.py and the GitHub settings in config.py
====================================================================
App JWT generation, installation-token exchange and caching, authenticated
requests, failure classification, and private-key loading.
"""

import time

import jwt
import pytest
import requests

import config
from services.github_app_service import GitHubAPIError, GitHubAppService, GitHubConfigError
from tests.github.conftest import APP_ID, INSTALLATION_ID, INSTALLATION_TOKEN, make_response


# ── Configuration ───────────────────────────────────────────────────────────

def test_integration_is_disabled_by_default():
    assert config.github_integration_enabled() is False


def test_complete_configuration_has_no_problems(github_env):
    assert config.github_config_problems() == []


def test_missing_values_are_named(monkeypatch):
    for name in ("GITHUB_APP_ID", "GITHUB_WEBHOOK_SECRET", "GITHUB_PRIVATE_KEY_PEM",
                 "GITHUB_PRIVATE_KEY_PATH"):
        monkeypatch.setenv(name, "")
    problems = " ".join(config.github_config_problems())
    assert "GITHUB_APP_ID" in problems
    assert "GITHUB_WEBHOOK_SECRET" in problems
    assert "GITHUB_PRIVATE_KEY_PEM or GITHUB_PRIVATE_KEY_PATH" in problems


def test_only_l0_autonomy_is_accepted(github_env, monkeypatch):
    monkeypatch.setenv("GITHUB_AUTONOMY_LEVEL", "L2")
    assert any("GITHUB_AUTONOMY_LEVEL" in p for p in config.github_config_problems())


def test_relative_key_path_resolves_against_backend_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)          # a working directory that is NOT backend/
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "./secrets/github-app.pem")
    assert config.github_private_key_path() == \
        (config.BACKEND_DIR / "secrets" / "github-app.pem").resolve()


def test_key_loads_from_a_file(monkeypatch, tmp_path, private_key_pem):
    key_file = tmp_path / "app.pem"
    key_file.write_text(private_key_pem)
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PEM", "")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    assert config.github_private_key() == private_key_pem


def test_pem_variable_wins_and_escaped_newlines_are_restored(monkeypatch, tmp_path,
                                                             private_key_pem):
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PEM", private_key_pem.replace("\n", "\\n"))
    assert config.github_private_key() == private_key_pem.strip()


def test_missing_key_file_is_reported_without_its_contents(github_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PEM", "")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    assert any("GITHUB_PRIVATE_KEY_PATH" in p for p in config.github_config_problems())


def test_from_config_refuses_an_incomplete_configuration(github_env, monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "")
    with pytest.raises(GitHubConfigError, match="GITHUB_APP_ID"):
        GitHubAppService.from_config()


# ── JWT ─────────────────────────────────────────────────────────────────────

def test_jwt_is_rs256_signed_by_the_app_key(app_service, public_key_pem):
    now = int(time.time())
    token = app_service.generate_jwt(now=now)
    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    claims = jwt.decode(token, public_key_pem, algorithms=["RS256"],
                        options={"verify_iat": False})
    assert claims["iss"] == APP_ID
    assert claims["iat"] <= now - 30                    # backdated for clock drift
    assert claims["exp"] - claims["iat"] <= 600         # GitHub's 10-minute cap


def test_unusable_key_raises_without_echoing_it(fake_github):
    bogus = "-----BEGIN RSA PRIVATE KEY-----\nNOTAKEY\n-----END RSA PRIVATE KEY-----"
    service = GitHubAppService(APP_ID, bogus, session=fake_github)
    with pytest.raises(GitHubConfigError) as info:
        service.generate_jwt()
    assert "NOTAKEY" not in str(info.value)
    assert info.value.__cause__ is None


def test_repr_never_shows_the_key(app_service, private_key_pem):
    assert private_key_pem.splitlines()[1] not in repr(app_service)


# ── Installation tokens ─────────────────────────────────────────────────────

def test_installation_token_exchange_uses_the_app_jwt(app_service, fake_github,
                                                      public_key_pem):
    assert app_service.get_installation_token(INSTALLATION_ID) == INSTALLATION_TOKEN
    [call] = fake_github.calls
    assert call["method"] == "POST"
    assert call["path"] == f"/app/installations/{INSTALLATION_ID}/access_tokens"
    scheme, app_jwt = call["headers"]["Authorization"].split(" ", 1)
    assert scheme == "Bearer"
    jwt.decode(app_jwt, public_key_pem, algorithms=["RS256"], options={"verify_iat": False})
    assert call["headers"]["X-GitHub-Api-Version"] == config.DEFAULT_GITHUB_API_VERSION
    assert call["headers"]["Accept"] == "application/vnd.github+json"
    assert call["timeout"]


def test_api_version_is_configurable(github_env, monkeypatch, fake_github):
    monkeypatch.setenv("GITHUB_API_VERSION", "2099-01-01")
    GitHubAppService.from_config(session=fake_github).get_installation_token(INSTALLATION_ID)
    assert fake_github.calls[0]["headers"]["X-GitHub-Api-Version"] == "2099-01-01"


def test_installation_token_is_cached(app_service, fake_github):
    app_service.get_installation_token(INSTALLATION_ID)
    app_service.get_installation_token(INSTALLATION_ID)
    assert len(fake_github.paths("POST")) == 1


def test_token_near_expiry_is_refreshed(app_service, fake_github):
    soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))
    fake_github.on("POST", r"/app/installations/\d+/access_tokens",
                   lambda call: make_response(201, {"token": "ghs_short", "expires_at": soon}))
    app_service.get_installation_token(INSTALLATION_ID)
    app_service.get_installation_token(INSTALLATION_ID)
    assert len(fake_github.paths("POST")) == 2


def test_authentication_failure_is_not_retryable(app_service, fake_github):
    fake_github.on("POST", r"/app/installations/\d+/access_tokens",
                   lambda call: make_response(401, {"message": "A JSON web token could not be decoded"}))
    with pytest.raises(GitHubAPIError) as info:
        app_service.get_installation_token(INSTALLATION_ID)
    assert info.value.status == 401
    assert info.value.retryable is False
    assert "could not be decoded" in info.value.message


def test_unknown_installation_is_not_retryable(app_service, fake_github):
    fake_github.on("POST", r"/app/installations/\d+/access_tokens",
                   lambda call: make_response(404, {"message": "Not Found"}))
    with pytest.raises(GitHubAPIError) as info:
        app_service.get_installation_token(INSTALLATION_ID)
    assert (info.value.status, info.value.retryable) == (404, False)


# ── Failure classification ──────────────────────────────────────────────────

@pytest.mark.parametrize("status,headers,retryable", [
    (500, {}, True),
    (502, {}, True),
    (429, {}, True),
    (403, {"X-RateLimit-Remaining": "0"}, True),
    (403, {}, False),
    (422, {}, False),
])
def test_http_failures_are_classified(app_service, fake_github, status, headers, retryable):
    fake_github.on("GET", r"/repos/o/r",
                   lambda call: make_response(status, {"message": "nope"}, headers))
    with pytest.raises(GitHubAPIError) as info:
        app_service.request("GET", "repos/o/r", INSTALLATION_ID)
    assert info.value.status == status
    assert info.value.retryable is retryable


@pytest.mark.parametrize("exc", [requests.Timeout("slow"), requests.ConnectionError("down")])
def test_network_failures_are_retryable(app_service, fake_github, exc):
    def boom(call):
        raise exc
    fake_github.on("GET", r"/repos/o/r", boom)
    with pytest.raises(GitHubAPIError) as info:
        app_service.request("GET", "repos/o/r", INSTALLATION_ID)
    assert info.value.retryable is True
    assert info.value.status is None


def test_a_401_is_retried_once_with_a_fresh_token(app_service, fake_github):
    responses = iter([make_response(401, {"message": "Bad credentials"}),
                      make_response(200, {"ok": True})])
    fake_github.on("GET", r"/repos/o/r", lambda call: next(responses))
    assert app_service.request("GET", "repos/o/r", INSTALLATION_ID) == {"ok": True}
    assert fake_github.paths("POST").count(
        f"/app/installations/{INSTALLATION_ID}/access_tokens") == 2


def test_requests_use_the_installation_token(app_service, fake_github):
    fake_github.on("GET", r"/repos/o/r", lambda call: make_response(200, {}))
    app_service.request("GET", "repos/o/r", INSTALLATION_ID)
    get_call = [c for c in fake_github.calls if c["method"] == "GET"][0]
    assert get_call["headers"]["Authorization"] == f"Bearer {INSTALLATION_TOKEN}"


def test_errors_never_contain_the_token(app_service, fake_github):
    fake_github.on("GET", r"/repos/o/r", lambda call: make_response(500, {"message": "boom"}))
    with pytest.raises(GitHubAPIError) as info:
        app_service.request("GET", "repos/o/r", INSTALLATION_ID)
    assert INSTALLATION_TOKEN not in str(info.value)
