# ============================================================
# CUQA Agent - Dockerfile
# Python 3.11-slim + FastAPI + Uvicorn + javalang + tree-sitter
# ============================================================


# ------------------------------------------------------------
# Stage 1 - Builder
# Compiles/installs all Python packages so the final image
# only copies the pre-built wheels, keeping it slim.
# ------------------------------------------------------------
FROM python:3.11-slim AS builder

# Build tools needed by some C-extension packages (lxml, tree-sitter)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Upgrade packaging tools
RUN python -m pip install --no-cache-dir --upgrade \
    pip \
    setuptools \
    wheel

# Install all CUQA runtime dependencies into /install/packages so they
# can be copied cleanly into the lightweight runtime stage.
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    "fastapi>=0.109.0" \
    "uvicorn[standard]>=0.27.0" \
    "gunicorn>=21.0.0" \
    "pydantic>=2.0.0" \
    "requests>=2.31.0" \
    "python-multipart>=0.0.9" \
    "javalang>=0.13.0" \
    "tree-sitter-language-pack>=0.0.1"


# ------------------------------------------------------------
# Stage 2 - Runtime
# Minimal image: no compilers, no build caches.
# ------------------------------------------------------------
FROM python:3.11-slim AS runtime


# ------------------------------------------------------------
# Create non-root application user
# Running as root inside a container is a security risk.
# ------------------------------------------------------------
RUN groupadd --gid 1001 cuqa && \
    useradd \
        --uid 1001 \
        --gid 1001 \
        --shell /bin/sh \
        --create-home \
        cuqa


# Application working directory
WORKDIR /app


# ------------------------------------------------------------
# Copy installed packages from the builder stage
# ------------------------------------------------------------
COPY --from=builder /install/packages /usr/local


# ------------------------------------------------------------
# Copy application source
# Only the files the server actually needs at runtime.
# ------------------------------------------------------------
COPY --chown=cuqa:cuqa src/ ./src/


# ------------------------------------------------------------
# Python runtime configuration
# ------------------------------------------------------------

# Immediately flush stdout/stderr - essential for container logs
ENV PYTHONUNBUFFERED=1

# Avoid writing .pyc bytecode files into the image layer
ENV PYTHONDONTWRITEBYTECODE=1

# Make the src/ package importable without an explicit sys.path hack
ENV PYTHONPATH=/app/src

# Application port (matches main.py uvicorn default)
ENV PORT=8080


# ------------------------------------------------------------
# Outputs directory
# CUQA writes temporary workspace files here; create it and
# give the cuqa user write permission before dropping privileges.
# ------------------------------------------------------------
RUN mkdir -p /app/outputs && chown -R cuqa:cuqa /app/outputs


# ------------------------------------------------------------
# Switch to non-root user
# ------------------------------------------------------------
USER cuqa


# ------------------------------------------------------------
# Expose application port
# ------------------------------------------------------------
EXPOSE 8080


# ------------------------------------------------------------
# Start the CUQA FastAPI server
#
# Gunicorn acts as the process manager; each worker runs the
# FastAPI app via the uvicorn worker class.
#
# Worker count = 1 is intentional: CUQA holds a single shared
# workspace state dict (_workspace) that must not be forked.
# Increase only if workspace state is moved to Redis/DB.
# ------------------------------------------------------------
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--threads", "2", \
     "--timeout", "180", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output", \
     "src.main:app"]
