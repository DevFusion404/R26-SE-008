# ============================================================
# Orchestration Agent (DIWO) - Dockerfile
# Python 3.11-slim + Flask + Gunicorn + git
#
# The DIWO backend sits between the React frontend and the
# three specialised agents (CUQA / RDP / SCTVA). It:
#   - manages the 5-stage refactoring workflow
#   - persists audit data to SQLite (runtime/database/)
#   - runs git commands via subprocess for repo operations
# ============================================================


# ------------------------------------------------------------
# Stage 1 - Builder
# ------------------------------------------------------------
FROM python:3.11-slim AS builder

# git is needed at build time only to satisfy any VCS-based
# pip dependencies; at runtime it is installed in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Install runtime dependencies into /install/packages
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    -r requirements.txt

# Gunicorn is the production WSGI server
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    "gunicorn>=21.0.0"


# ------------------------------------------------------------
# Stage 2 - Runtime
# ------------------------------------------------------------
FROM python:3.11-slim AS runtime

# git is required at runtime by git_service.py which shells
# out to `git` for clone, commit, diff and branch operations.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*


# ------------------------------------------------------------
# Create non-root application user
# ------------------------------------------------------------
RUN groupadd --gid 1001 diwo && \
    useradd \
        --uid 1001 \
        --gid 1001 \
        --shell /bin/sh \
        --create-home \
        diwo


WORKDIR /app


# ------------------------------------------------------------
# Copy installed packages from builder
# ------------------------------------------------------------
COPY --from=builder /install/packages /usr/local


# ------------------------------------------------------------
# Copy application source
# The Dockerfile lives in backend/ so every COPY is relative
# to that directory (the Docker build context).
# ------------------------------------------------------------
COPY --chown=diwo:diwo api/        ./api/
COPY --chown=diwo:diwo clients/    ./clients/
COPY --chown=diwo:diwo db/         ./db/
COPY --chown=diwo:diwo domain/     ./domain/
COPY --chown=diwo:diwo services/   ./services/
COPY --chown=diwo:diwo app.py      ./
COPY --chown=diwo:diwo config.py   ./


# ------------------------------------------------------------
# Runtime directories
#
# SQLite database, saved reports and refactored-source archives
# are all written under /app/runtime/ at runtime.
# Mount a persistent volume here so data survives container
# restarts:
#   docker run -v diwo-data:/app/runtime diwo-agent
# ------------------------------------------------------------
RUN mkdir -p /app/runtime/database \
             /app/runtime/reports \
             /app/runtime/archives && \
    chown -R diwo:diwo /app/runtime

# Git clone root (remote repos are cloned here by git_service.py)
RUN mkdir -p /home/diwo/DIWO/repos && \
    chown -R diwo:diwo /home/diwo/DIWO


# ------------------------------------------------------------
# Python / Flask configuration
# ------------------------------------------------------------
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV FLASK_ENV=production
ENV FLASK_APP=app.py

# Default port — matches config.py DIWO_PORT default
ENV DIWO_PORT=5001
ENV PORT=5001

# Downstream agent URLs — override these at container runtime
# to point at your actual deployed CUQA / RDP / SCTVA services.
ENV CUQA_AGENT_URL=http://cuqa-agent:8080
ENV RDP_AGENT_URL=http://rdp-agent:5000
ENV SCTVA_AGENT_URL=http://sctva-agent:8002

# SQLite DB lives inside the runtime volume
ENV DIWO_RUNTIME_DIR=/app/runtime

# Production: disable debug mode
ENV FLASK_DEBUG=0


# ------------------------------------------------------------
# Switch to non-root user
# ------------------------------------------------------------
USER diwo


# ------------------------------------------------------------
# Expose application port
# ------------------------------------------------------------
EXPOSE 5001


# ------------------------------------------------------------
# Start Flask through Gunicorn
#
# 1 worker is intentional: SQLite does not support concurrent
# writes from multiple processes. Increase workers only after
# migrating to Postgres/Supabase.
# ------------------------------------------------------------
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:5001", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output", \
     "app:create_app()"]
