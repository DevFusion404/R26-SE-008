# ============================================================
# SCTVA - Safe Code Transformation & Validation Agent
# Dockerfile
# Python 3.11-slim + Flask + Gunicorn + libcst
# ============================================================


# ------------------------------------------------------------
# Stage 1 - Builder
# Installs all Python packages into /install/packages so only
# the pre-built wheels are copied into the lean runtime image.
# ------------------------------------------------------------
FROM python:3.11-slim AS builder

# libcst requires no C extensions so only basic build tools are needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Upgrade packaging tools
RUN python -m pip install --no-cache-dir --upgrade \
    pip \
    setuptools \
    wheel

# Install all SCTVA runtime dependencies
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    "flask>=3.0.0" \
    "flask-cors>=4.0.0" \
    "gunicorn>=21.0.0" \
    "libcst>=1.4.0"


# ------------------------------------------------------------
# Stage 2 - Runtime
# Minimal image: no compilers, no build caches.
# ------------------------------------------------------------
FROM python:3.11-slim AS runtime


# ------------------------------------------------------------
# Create non-root application user
# ------------------------------------------------------------
RUN groupadd --gid 1001 sctva && \
    useradd \
        --uid 1001 \
        --gid 1001 \
        --shell /bin/sh \
        --create-home \
        sctva


# Application working directory
WORKDIR /app


# ------------------------------------------------------------
# Copy installed packages from the builder stage
# ------------------------------------------------------------
COPY --from=builder /install/packages /usr/local


# ------------------------------------------------------------
# Copy application source
# ------------------------------------------------------------
COPY --chown=sctva:sctva sctva/    ./sctva/
COPY --chown=sctva:sctva static/   ./static/
COPY --chown=sctva:sctva app.py    ./


# ------------------------------------------------------------
# Python / Flask configuration
# ------------------------------------------------------------

# Immediately flush stdout/stderr - essential for container logs
ENV PYTHONUNBUFFERED=1

# Avoid .pyc files in the image layers
ENV PYTHONDONTWRITEBYTECODE=1

# Flask settings
ENV FLASK_ENV=production
ENV FLASK_APP=app.py

# Application port (matches app.py SCTVA_PORT default)
ENV SCTVA_PORT=8002
ENV PORT=8002


# ------------------------------------------------------------
# Runtime directory for temp workspaces
# SCTVA writes scratch files here during transformations.
# ------------------------------------------------------------
RUN mkdir -p /app/.sctva_runtime && chown -R sctva:sctva /app/.sctva_runtime


# ------------------------------------------------------------
# Switch to non-root user
# ------------------------------------------------------------
USER sctva


# ------------------------------------------------------------
# Expose application port
# ------------------------------------------------------------
EXPOSE 8002


# ------------------------------------------------------------
# Start Flask through Gunicorn
#
# 1 worker is intentional - SCTVA holds mutable in-process
# workspace state. Increase only when state is externalised.
# ------------------------------------------------------------
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:8002", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output", \
     "app:create_app()"]
