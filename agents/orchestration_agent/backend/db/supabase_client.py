"""
Supabase client
===============
R26-SE-008 | Bandara S M Y M | IT22277886

The one place this service builds a Supabase connection, mirroring
user_management/config/supabase_client.py so the two services are configured
by the same variables and fail the same way.

    SUPABASE_URL                 https://<project-ref>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY    preferred here — see below
    SUPABASE_KEY / _ANON_KEY     fallbacks, in that order

SERVICE ROLE, NOT ANON. The orchestrator is a trusted backend with no end-user
session attached to a request: it writes audit rows, feedback rows and impact
records on behalf of a workflow rather than on behalf of a signed-in user. The
anon key is evaluated against row-level security policies written for browser
callers, which would reject exactly those writes — quietly, as an empty result
rather than an exception. The service role key bypasses RLS, which is why it
must never reach the frontend or a committed file.

LAZY, and a singleton. Importing this module must not require credentials:
`import config` runs at startup on every deployment including SQLite ones, and
a module that connected at import time would make Supabase a hard dependency of
simply starting the process.
"""

import base64
import json
import logging
import os
import re

from dotenv import load_dotenv

import config

# The reference implementation loads .env here, and this service has no other
# loader — config.py reads os.environ directly and documents that a .env file
# is NOT picked up on its own. Loading it at this one point means the Supabase
# variables behave the way user_management's do.
load_dotenv()

logger = logging.getLogger("diwo.supabase")

_client = None


class SupabaseUnavailable(RuntimeError):
    """Supabase was asked for and could not be reached or configured."""


def supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", config.SUPABASE_URL).strip()


def supabase_key() -> str:
    return (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or config.SUPABASE_KEY
    ).strip()


def get_supabase():
    """The shared client, built on first use.

    Raises SupabaseUnavailable rather than returning None, so a caller cannot
    accidentally treat "no database" as "no rows".
    """
    global _client
    if _client is not None:
        return _client

    url, key = supabase_url(), supabase_key()
    if not url or not key:
        raise SupabaseUnavailable(
            "Supabase credentials missing: set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) in the environment."
        )

    problem = url_problem(url)
    if problem:
        raise SupabaseUnavailable(problem)

    try:
        from supabase import create_client
    except ImportError as exc:   # pragma: no cover - deployment problem, not logic
        raise SupabaseUnavailable(
            "The 'supabase' package is not installed. Add it with: "
            "pip install 'supabase>=2.0.0'"
        ) from exc

    _client = create_client(url, key)
    logger.info("Supabase client initialized for %s", url)
    return _client


def reset_client():
    """Drop the cached client. Only for tests that swap credentials."""
    global _client
    _client = None


# ─── Configuration checks ────────────────────────────────────────────────────
#
# Both of these exist because the failure they catch does not look like a
# configuration failure from the outside. A bad URL surfaces as a 500 on every
# request, and a public key surfaces as reads that return nothing and writes
# that are refused by RLS — which reaches the developer as "the Continue button
# does nothing", several screens away from the cause.


def url_problem(url: str) -> str:
    """Why this SUPABASE_URL cannot work, or "" when it is fine.

    The near-universal mistake is pasting the Postgres connection string from
    Project Settings -> Database. supabase-py wants the REST base it builds
    /rest/v1 onto, which is the PROJECT URL under Settings -> API.
    """
    if url.startswith(("postgres://", "postgresql://")):
        ref = ""
        match = re.search(r"db\.([a-z0-9]+)\.supabase\.co", url)
        if match:
            ref = f" For this project that is https://{match.group(1)}.supabase.co"
        return (
            "SUPABASE_URL is a Postgres connection string, not the project URL. "
            "supabase-py talks to PostgREST over HTTPS, so it needs the URL from "
            "Project Settings -> API -> Project URL." + ref
        )

    if not url.startswith(("http://", "https://")):
        return (
            f"SUPABASE_URL must start with https:// — got {url!r}. "
            "Copy it from Project Settings -> API -> Project URL."
        )

    return ""


def key_is_public(key: str) -> bool:
    """True when this looks like a publishable/anon key rather than a secret one.

    Two key formats are in circulation. The current one is prefixed —
    sb_publishable_... versus sb_secret_... — and the legacy one is a JWT whose
    unverified payload carries {"role": "anon"} or {"role": "service_role"}.
    The payload is read WITHOUT verifying the signature on purpose: this is a
    hint about which key was pasted, not an authentication decision.
    """
    if key.startswith(("sb_publishable_", "sb_pub")):
        return True

    if key.startswith("eyJ"):
        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return claims.get("role") == "anon"
        except Exception:
            return False

    return False


def startup_check() -> str:
    """One cheap read, so a broken configuration is known at boot.

    Returns a human-readable status line. Never raises: a service that refuses
    to start on a database hiccup is worse than one that starts and says the
    database is unreachable.
    """
    try:
        client = get_supabase()
    except SupabaseUnavailable as exc:
        return f"Supabase NOT usable — {exc}"

    warning = ""
    if key_is_public(supabase_key()):
        warning = (
            "  WARNING: the key looks like a PUBLISHABLE/anon key. This backend "
            "writes on behalf of a workflow, not a signed-in user, so row-level "
            "security will reject those writes — reads will simply return "
            "nothing. Use the SECRET / service_role key "
            "(Project Settings -> API)."
        )

    try:
        client.table("workflows").select("id").limit(1).execute()
    except Exception as exc:   # noqa: BLE001 - any transport/API error is a status
        return f"Supabase unreachable at {supabase_url()} — {exc}{warning}"

    return f"Supabase ready at {supabase_url()}.{warning}"
