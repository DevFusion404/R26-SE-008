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


def cors_origins():
    """CORS origins in the shape flask-cors expects."""
    if CORS_ORIGINS.strip() == "*":
        return "*"
    return [origin.strip() for origin in CORS_ORIGINS.split(",") if origin.strip()]
