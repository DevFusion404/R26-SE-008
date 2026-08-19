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

Values are read from the process environment only — the same source the
clients used before this module existed. A .env file is *not* auto-loaded
(python-dotenv is not a dependency), so exporting the variables, or setting
them in the run configuration, is what takes effect. See .env.example.
"""

import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

BACKEND_DIR = Path(__file__).resolve().parent

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


#: Remote repositories are cloned here, not into a temp dir: GitHub Desktop has
#: to be able to open the same working copy afterwards, and a second run on the
#: same repository should reuse the clone instead of downloading it again.
CLONE_ROOT = Path(os.environ.get("DIWO_CLONE_ROOT") or (Path.home() / "DIWO" / "repos"))


# ─────────────────────────────────────────────────────────────────────────────
# Specialized agent endpoints
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CUQA_URL = "http://localhost:8080"
DEFAULT_RDP_URL = "http://localhost:5000"
DEFAULT_SCTVA_URL = "http://localhost:8002"


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
