"""
Orchestration Agent configuration
=================================
R26-SE-008 | Bandara S M Y M | IT22277886

Every URL, port and filesystem path the DIWO backend depends on is resolved
here, so no module has to carry its own hard-coded localhost address.

Ports are the ones the agents actually start on, verified against each agent's
own entry point rather than assumed:

    CUQA   http://localhost:8080   agents/cuqa_agent/src/main.py    (uvicorn, port=8080)
    RDP    http://localhost:5000   agents/rdp_agent/app.py          (app.run, port=5000)
    SCTVA  http://localhost:8002   agents/transformation_agent/.../app.py (SCTVA_PORT, default 8002)
    DIWO   http://localhost:5001   this backend

backend/.env IS loaded, by load_dotenv() below, before any value here is read.
A real environment variable still wins over the file — python-dotenv does not
override what is already set — so a container can override the checked-out
.env without editing it. See .env.example for every name this file reads.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent

# Load local backend environment configuration.
load_dotenv(BACKEND_DIR / ".env")

#: Generated data lives under runtime/ so it is never mixed with source.
#: DIWO_RUNTIME_DIR redirects the whole tree, which is how the tests keep their
#: databases, reports and archives out of the real one.
RUNTIME_DIR = Path(os.environ.get("DIWO_RUNTIME_DIR") or (BACKEND_DIR / "runtime"))
DATABASE_DIR = RUNTIME_DIR / "database"
REPORTS_DIR = RUNTIME_DIR / "reports"
ARCHIVES_DIR = RUNTIME_DIR / "archives"

#: Where diwo_audit.db used to sit, before runtime/ existed. Kept so an older
#: working copy migrates its workflow history instead of starting empty.
LEGACY_DB_PATH = BACKEND_DIR / "diwo_audit.db"

DB_FILENAME = "diwo_audit.db"


def database_path() -> Path:
    """Absolute path of the SQLite audit database.

    Overridable with DIWO_DB_PATH. Otherwise runtime/database/diwo_audit.db,
    with a one-time move of a legacy backend/diwo_audit.db into place so no
    existing workflow history is lost.
    """
    override = os.environ.get("DIWO_DB_PATH")
    if override:
        path = Path(override).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    path = DATABASE_DIR / DB_FILENAME

    if not path.exists() and LEGACY_DB_PATH.exists():
        LEGACY_DB_PATH.replace(path)
        print(f"[DIWO] Migrated {LEGACY_DB_PATH.name} to {path}")

    return path


def reports_dir() -> Path:
    """Where saved updated-smell-report JSON files are written."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


def archives_dir() -> Path:
    """Where per-workflow refactored-source ZIPs are kept."""
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    return ARCHIVES_DIR


# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────
#
# TWO BACKENDS, ONE SEAM. Every persistence call in this service goes through
# db/workflow_repository.py, which dispatches to either db/sqlite_repository.py
# (the default) or db/supabase_repository.py. Nothing outside db/ knows which
# one is running, so switching is a matter of environment variables.
#
# Supabase is selected when SUPABASE_URL and a key are both present — the same
# pair user_management/config/supabase_client.py reads. Setting
# DATABASE_PROVIDER=supabase without them is a misconfiguration and is reported
# at startup rather than silently falling back.
#
# DATABASE_URL below is the raw Postgres DSN, which this service does NOT use:
# the Supabase backend talks to PostgREST over HTTPS through the supabase-py
# client, exactly as user_management does. The DSN is kept declared for a
# future direct-psycopg port and for tooling that wants it.

#: "sqlite" (the default) or "supabase" / "postgres".
DATABASE_PROVIDER = os.environ.get("DATABASE_PROVIDER", "sqlite").strip().lower()

#: Supabase project URL, e.g. https://abcdefgh.supabase.co
#: Project Settings -> API -> Project URL.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()

#: The API key, in the order user_management resolves it.
#:
#: PREFER THE SERVICE ROLE KEY for this service. The orchestrator is a trusted
#: backend with no end-user session: it writes audit rows on behalf of the
#: workflow, and the anon key is subject to row-level security policies written
#: for browser callers, which would silently reject those writes.
#: Project Settings -> API -> service_role. It bypasses RLS, so it must never
#: reach the frontend or a committed file.
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
    or ""
).strip()

#: Full Postgres DSN. Supabase gives you this under Project Settings ->
#: Database -> Connection string. It contains the password, so it belongs in
#: .env and never in .env.example.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

#: "session" (port 5432, supports prepared statements) or "transaction"
#: (port 6543, does not). psycopg prepares statements by default, so a
#: transaction-mode pooler needs prepare_threshold=None on the connection.
DB_POOL_MODE = os.environ.get("DB_POOL_MODE", "session").strip().lower()


def uses_postgres() -> bool:
    """True when a raw Postgres DSN has been supplied.

    Nothing branches on this: the Supabase backend goes through PostgREST, not
    the DSN. Kept so a future direct-psycopg port has its switch already named.
    """
    return DATABASE_PROVIDER in ("supabase", "postgres", "postgresql") and bool(DATABASE_URL)


def uses_supabase() -> bool:
    """True when this process should persist through Supabase instead of SQLite.

    Credentials are the deciding fact, not the provider name — a service told
    to use Supabase with no key cannot, and a service given both plainly can.
    DATABASE_PROVIDER only has to not say "sqlite", so pointing an existing
    deployment at Supabase is a matter of adding the two variables.
    """
    if DATABASE_PROVIDER == "sqlite":
        return False
    return bool(SUPABASE_URL and SUPABASE_KEY)


def database_backend() -> str:
    """Which backend is live: "supabase" or "sqlite". For logs and /api/health."""
    return "supabase" if uses_supabase() else "sqlite"


def supabase_misconfigured() -> str:
    """The reason Supabase was asked for but cannot be used, or "" when fine."""
    if DATABASE_PROVIDER in ("supabase", "postgres", "postgresql") and not uses_supabase():
        missing = []
        if not SUPABASE_URL:
            missing.append("SUPABASE_URL")
        if not SUPABASE_KEY:
            missing.append("SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY)")
        return (
            f"DATABASE_PROVIDER={DATABASE_PROVIDER} but {' and '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set — falling back to SQLite."
        )
    return ""


#: Remote repositories are cloned here, not into a temp dir: GitHub Desktop has
#: to be able to open the same working copy afterwards, and a second run on the
#: same repository should reuse the clone instead of downloading it again.
CLONE_ROOT = Path(os.environ.get("DIWO_CLONE_ROOT") or (Path.home() / "DIWO" / "repos"))


# ─────────────────────────────────────────────────────────────────────────────
# Specialized agent endpoints
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CUQA_URL = os.environ.get("CUQA_AGENT_URL") or os.environ.get("CUQA_URL") or "https://cuqaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io"
DEFAULT_RDP_URL = os.environ.get("RDP_AGENT_URL") or os.environ.get("RDP_URL") or "https://rdpagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io"
DEFAULT_SCTVA_URL = os.environ.get("SCTVA_AGENT_URL") or os.environ.get("SCTVA_URL") or "https://sctvaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io"


def cuqa_base_url() -> str:
    """Base URL of the CUQA agent (CUQA_AGENT_URL)."""
    return os.environ.get("CUQA_AGENT_URL", DEFAULT_CUQA_URL).rstrip("/")


def rdp_base_url() -> str:
    """Base URL of the RDP agent (RDP_AGENT_URL)."""
    return os.environ.get("RDP_AGENT_URL", DEFAULT_RDP_URL).rstrip("/")


def sctva_base_url() -> str:
    """Base URL of the SCTVA agent (SCTVA_AGENT_URL)."""
    return os.environ.get("SCTVA_AGENT_URL", DEFAULT_SCTVA_URL).rstrip("/")


def _seconds(name: str, default: int, minimum: int = 10) -> int:
    try:
        return max(minimum, int(os.environ.get(name, "").strip() or default))
    except ValueError:
        return default


def sctva_execute_timeout() -> int:
    """Seconds to wait for /sctva/execute (SCTVA_EXECUTE_TIMEOUT_SECONDS).

    SCTVA parses, transforms and validates every planned file in one request,
    so a large project needs minutes, not the 120 s the other agent calls use.
    """
    return _seconds("SCTVA_EXECUTE_TIMEOUT_SECONDS", 600)


def rdp_generate_timeout() -> int:
    """Seconds to wait for the RDP agent's /generate (RDP_GENERATE_TIMEOUT_SECONDS).

    RDP scores every selected smell; a large selection (hundreds of smells)
    needs minutes. When it does not answer in time DIWO falls back to its own
    plan, which carries no RDP scoring evidence.
    """
    return _seconds("RDP_GENERATE_TIMEOUT_SECONDS", 600)


def source_snapshot_max_bytes() -> int:
    """Upper bound on the source kept per workflow at smell selection
    (DIWO_SOURCE_SNAPSHOT_MAX_MB, default 8). Files past it are read live."""
    return _seconds("DIWO_SOURCE_SNAPSHOT_MAX_MB", 8, minimum=1) * 1024 * 1024


def source_fetch_timeout() -> int:
    """Seconds to wait when reading the planned files' source text before the
    transformation (DIWO_SOURCE_FETCH_TIMEOUT_SECONDS)."""
    return _seconds("DIWO_SOURCE_FETCH_TIMEOUT_SECONDS", 180)


# ─────────────────────────────────────────────────────────────────────────────
# Flask application
# ─────────────────────────────────────────────────────────────────────────────

#: Mount point of every blueprint. The React frontend and the Vite dev proxy
#: both address this backend as /api/..., so it must not change.
API_PREFIX = "/api"

SECRET_KEY = os.environ.get("DIWO_SECRET_KEY", "diwo-prototype-secret-2026")

#: Comma-separated origins, or "*" (the prototype default) for any origin.
CORS_ORIGINS = os.environ.get("DIWO_CORS_ORIGINS", "*")

HOST = os.environ.get("DIWO_HOST", "0.0.0.0")
PORT = int(os.environ.get("DIWO_PORT", "5001"))
DEBUG = os.environ.get("FLASK_DEBUG", "1") not in ("0", "false", "False")

#: Guard rails so a malformed payload cannot exhaust memory building an archive.
MAX_ARCHIVE_FILES = 2000
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024

GIT_TIMEOUT = 300


def git_push_token() -> str:
    """Server-side GitHub token for clone / fetch / push (DIWO_GIT_PUSH_TOKEN).

    A fine-grained personal access token (Contents: read & write) or a classic
    token with `repo` scope. Needed on a hosted backend, which has no credential
    helper and no one at a terminal to answer git's username prompt. Set it as
    a secret, never in a committed .env.
    """
    return (os.environ.get("DIWO_GIT_PUSH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def open_github_desktop_enabled() -> bool:
    """Whether apply-and-push launches GitHub Desktop (DIWO_OPEN_GITHUB_DESKTOP).

    Only meaningful when the backend runs on the developer's own machine. A
    container has no desktop to open it on, so the default is on for Windows
    and macOS and off everywhere else.
    """
    value = os.environ.get("DIWO_OPEN_GITHUB_DESKTOP")
    if value is None or not value.strip():
        return os.name == "nt" or sys.platform == "darwin"
    return value.strip().lower() in ("1", "true", "yes", "on")


def cors_origins():
    """CORS origins in the shape flask-cors expects."""
    if CORS_ORIGINS.strip() == "*":
        return "*"
    return [origin.strip() for origin in CORS_ORIGINS.split(",") if origin.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# GitHub App integration
# ─────────────────────────────────────────────────────────────────────────────
#
# OFF BY DEFAULT. Nothing GitHub-related runs until GITHUB_INTEGRATION_ENABLED
# is true, and once it is, github_config_problems() names every missing value.
#
# Every value is read per call rather than frozen at import, so a container can
# be reconfigured by a restart and a test can flip the integration on and off.
# None of these helpers ever returns a secret inside an error message.

GITHUB_API_URL = "https://api.github.com"

#: The REST API version DIWO was written and tested against. It is sent as
#: X-GitHub-Api-Version and can be overridden with GITHUB_API_VERSION rather
#: than edited here, so moving to a newer supported version is a config change.
DEFAULT_GITHUB_API_VERSION = "2022-11-28"

#: L0: every refactoring change requires an explicit developer approval. It is
#: the only level implemented; higher levels are rejected by validation rather
#: than silently treated as L0.
SUPPORTED_AUTONOMY_LEVELS = ("L0",)

#: Name of the Check Run DIWO publishes on each pull request.
GITHUB_CHECK_NAME = "RefactorIQ / DIWO"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def github_integration_enabled() -> bool:
    return _env_flag("GITHUB_INTEGRATION_ENABLED", False)


def github_app_id() -> str:
    return os.environ.get("GITHUB_APP_ID", "").strip()


def github_webhook_secret() -> str:
    return os.environ.get("GITHUB_WEBHOOK_SECRET", "")


def github_api_version() -> str:
    return os.environ.get("GITHUB_API_VERSION", "").strip() or DEFAULT_GITHUB_API_VERSION


def github_autonomy_level() -> str:
    return os.environ.get("GITHUB_AUTONOMY_LEVEL", "L0").strip().upper() or "L0"


def github_private_key_path() -> Path | None:
    """GITHUB_PRIVATE_KEY_PATH, with a relative path resolved against backend/.

    Resolving against BACKEND_DIR rather than the working directory means
    `./secrets/github-app.pem` names the same file whether the process was
    started from backend/, the repository root, or gunicorn in /app.
    """
    raw = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


def github_private_key() -> str:
    """The App's PEM private key, or "" when none is configured.

    GITHUB_PRIVATE_KEY_PEM wins when set — that is how a Container Apps secret
    arrives. Escaped newlines are restored, because a PEM pasted into a
    single-line secret field usually arrives with literal "\\n" sequences.
    """
    pem = os.environ.get("GITHUB_PRIVATE_KEY_PEM", "").replace("\\n", "\n").strip()
    if pem:
        return pem

    path = github_private_key_path()
    if path is not None and path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def github_config_problems() -> list:
    """Every reason the enabled integration cannot run, or [] when it can.

    Names settings, never their values.
    """
    problems = []
    if not github_app_id():
        problems.append("GITHUB_APP_ID is not set.")
    if not github_webhook_secret():
        problems.append("GITHUB_WEBHOOK_SECRET is not set.")
    if not os.environ.get("GITHUB_PRIVATE_KEY_PEM", "").strip():
        path = github_private_key_path()
        if path is None:
            problems.append(
                "No private key: set GITHUB_PRIVATE_KEY_PEM or GITHUB_PRIVATE_KEY_PATH."
            )
        elif not path.is_file():
            problems.append(
                "GITHUB_PRIVATE_KEY_PATH does not point to a readable file."
            )
    if github_autonomy_level() not in SUPPORTED_AUTONOMY_LEVELS:
        problems.append(
            f"GITHUB_AUTONOMY_LEVEL must be one of {', '.join(SUPPORTED_AUTONOMY_LEVELS)}; "
            "higher autonomy levels are not implemented."
        )
    return problems


# ── PR analysis (Milestones 2-3) ─────────────────────────────────────────────

def diwo_frontend_url() -> str:
    """Public base URL of the React frontend, e.g. https://refactoriq.example.org.

    Used for the "Open in DIWO" link on GitHub Check Runs. Never guessed: when
    DIWO_FRONTEND_URL is unset the Check Run simply carries no link.
    """
    url = os.environ.get("DIWO_FRONTEND_URL", "").strip().rstrip("/")
    if url.startswith("https://") or url.startswith(("http://localhost", "http://127.0.0.1")):
        return url
    return ""


def github_snapshot_max_bytes() -> int:
    """Largest PR snapshot archive DIWO will download (compressed)."""
    return max(1, int(os.environ.get("GITHUB_SNAPSHOT_MAX_MB", "100"))) * 1024 * 1024


def github_cuqa_timeout_seconds() -> int:
    return max(10, int(os.environ.get("GITHUB_CUQA_TIMEOUT_SECONDS", "300")))


def github_cuqa_allow_shared_workspace() -> bool:
    """Fall back to CUQA's shared workspace when /api/analyze-snapshot is missing.

    OFF by default. The fallback uploads the PR snapshot through
    /api/upload-zip, which REPLACES the repository a developer has loaded
    interactively in CUQA. It is serialized and the report is verified to
    belong to the uploaded snapshot, but enable it only for a CUQA deployment
    that has not yet been updated with /api/analyze-snapshot.
    """
    return _env_flag("GITHUB_CUQA_ALLOW_SHARED_WORKSPACE", False)


# ── Access to GitHub-linked workflows ────────────────────────────────────────
#
# A workflow created from a GitHub PR describes code in a (possibly private)
# repository, so its id must not work as a bearer capability. Every route that
# names such a workflow requires a signed-in DIWO user (the Supabase access
# token the frontend already holds). Workflows created in the DIWO UI are
# unaffected.

def supabase_jwt_secret() -> str:
    """Supabase JWT secret (Project Settings -> API -> JWT secret).

    When set, access tokens are verified locally (HS256). When unset, they are
    verified by asking Supabase Auth, which needs SUPABASE_URL and a key.
    """
    return os.environ.get("SUPABASE_JWT_SECRET", "").strip()


def github_workflow_allowed_emails() -> set:
    """Optional allow-list of user e-mails for GitHub-linked workflows.

    Empty means any authenticated DIWO user.
    """
    raw = os.environ.get("GITHUB_WORKFLOW_ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


# ── Repository authorization for approvals (Milestone 4) ─────────────────────
#
# Reading a GitHub-linked workflow needs a signed-in user. APPROVING anything on
# it (smell selection, plan decisions) additionally needs a grant for that
# repository, from either source:
#
#   GITHUB_REPO_REVIEWERS   explicit grants:
#                           "octo-org/demo=dev@x.org|lead@x.org;octo-org/*=lead@x.org"
#   GITHUB_USER_LOGINS      DIWO e-mail -> GitHub login, verified LIVE against
#                           the repository: the login must have write, maintain
#                           or admin permission ("dev@x.org=octocat,...")
#
# With neither configured for a repository, approvals are refused unless
# GITHUB_REQUIRE_REPO_AUTHORIZATION=false (local demos only).

def _parse_pairs(raw: str, item_sep: str, kv_sep: str) -> list:
    pairs = []
    for item in (raw or "").split(item_sep):
        if kv_sep in item:
            key, value = item.split(kv_sep, 1)
            if key.strip() and value.strip():
                pairs.append((key.strip(), value.strip()))
    return pairs


def github_repo_reviewers() -> dict:
    """{"owner/repo" or "owner/*": {emails}} from GITHUB_REPO_REVIEWERS."""
    grants = {}
    for repo, emails in _parse_pairs(os.environ.get("GITHUB_REPO_REVIEWERS", ""), ";", "="):
        grants.setdefault(repo.lower(), set()).update(
            e.strip().lower() for e in emails.split("|") if e.strip())
    return grants


def github_user_logins() -> dict:
    """{email: github_login} from GITHUB_USER_LOGINS."""
    return {email.lower(): login
            for email, login in _parse_pairs(os.environ.get("GITHUB_USER_LOGINS", ""), ",", "=")}


def github_require_repo_authorization() -> bool:
    return _env_flag("GITHUB_REQUIRE_REPO_AUTHORIZATION", True)


def github_rdp_attach_sources() -> bool:
    """Send RDP the PR-commit source of the selected files (for Move Method)."""
    return _env_flag("GITHUB_RDP_ATTACH_SOURCES", True)


# ── Interactive GitHub refactoring (commands, SCTVA, write-back) ─────────────

def github_commands_enabled() -> bool:
    """React to /refactoriq PR comments and Check Run buttons."""
    return _env_flag("GITHUB_COMMANDS_ENABLED", True)


def github_pr_transformation_enabled() -> bool:
    """Run SCTVA on pull-request code after the developer approves a plan.

    OFF BY DEFAULT, for a security reason rather than a functional one:
    SCTVA's behavioural validation EXECUTES the code it validates (Python via
    exec, Java/C by compiling and running). PR code is untrusted, so enable
    this only when SCTVA runs in an isolated container — no secrets, no
    access to internal services, restricted egress, resource limits. DIWO
    never sends SCTVA any GitHub credential.
    """
    return _env_flag("GITHUB_PR_TRANSFORMATION_ENABLED", False)


def github_pr_writeback_enabled() -> bool:
    """Commit developer-ACCEPTED, validated changes to the PR branch.

    OFF BY DEFAULT. Even when on, nothing is committed without a recorded
    final developer acceptance and an explicit submit. Requires the App's
    Contents: Read and write permission.
    """
    return _env_flag("GITHUB_PR_WRITEBACK_ENABLED", False)


def github_confirmation_ttl_seconds() -> int:
    """How long a "submit … confirm" request stays valid after the prompt."""
    return max(60, int(os.environ.get("GITHUB_CONFIRMATION_TTL_SECONDS", "1800")))


# ── Background job processing ────────────────────────────────────────────────

def github_job_max_attempts() -> int:
    """How many times a job is tried before it is recorded as failed."""
    return max(1, int(os.environ.get("GITHUB_JOB_MAX_ATTEMPTS", "5")))


def github_job_lock_timeout_seconds() -> int:
    """How long a claimed job may run before another worker may reclaim it.

    This is what stops a worker crash from losing an accepted job: its claim
    simply expires and the job is picked up again.
    """
    return max(30, int(os.environ.get("GITHUB_JOB_LOCK_TIMEOUT_SECONDS", "600")))


def github_worker_poll_seconds() -> float:
    return max(1.0, float(os.environ.get("GITHUB_WORKER_POLL_SECONDS", "5")))


def github_embedded_worker_enabled() -> bool:
    """Run the job worker as a thread inside the web process.

    A convenience for local development and single-container demos only. The
    jobs are durable either way, but production should run github_worker.py as
    its own process so web traffic and job processing scale and fail apart.
    """
    return _env_flag("GITHUB_EMBEDDED_WORKER", False)
