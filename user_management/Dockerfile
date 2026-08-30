# ============================================================
# User Management Service - Azure & Production Dockerfile
# Python 3.11-slim + Flask + Gunicorn + Supabase Auth
# ============================================================


# ------------------------------------------------------------
# Stage 1 - Builder
# Installs Python packages into /install/packages to keep the
# final runtime image lean and secure.
# ------------------------------------------------------------
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade \
    pip \
    setuptools \
    wheel

WORKDIR /install

# Copy requirements file first to maximize build caching
COPY requirements.txt .

# Install all dependencies into a standalone prefix directory
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    -r requirements.txt


# ------------------------------------------------------------
# Stage 2 - Runtime
# Lightweight production image containing only runtime dependencies.
# ------------------------------------------------------------
FROM python:3.11-slim AS runtime

# ------------------------------------------------------------
# Create non-root application user for container security
# ------------------------------------------------------------
RUN groupadd --gid 1002 usermgt && \
    useradd \
        --uid 1002 \
        --gid 1002 \
        --shell /bin/sh \
        --create-home \
        usermgt

# Application working directory
WORKDIR /app

# ------------------------------------------------------------
# Copy installed Python packages from builder stage
# ------------------------------------------------------------
COPY --from=builder /install/packages /usr/local

# ------------------------------------------------------------
# Copy application source code
# ------------------------------------------------------------
COPY --chown=usermgt:usermgt config/ ./config/
COPY --chown=usermgt:usermgt middleware/ ./middleware/
COPY --chown=usermgt:usermgt models/ ./models/
COPY --chown=usermgt:usermgt routes/ ./routes/
COPY --chown=usermgt:usermgt services/ ./services/
COPY --chown=usermgt:usermgt app.py ./
COPY --chown=usermgt:usermgt requirements.txt ./

# ------------------------------------------------------------
# Python / Flask runtime configuration
# ------------------------------------------------------------
# Ensure stdout and stderr are unbuffered for immediate Azure log streaming
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

ENV FLASK_ENV=production
ENV FLASK_APP=app.py

# Default service port
ENV PORT=5005

# ------------------------------------------------------------
# Switch to non-root user
# ------------------------------------------------------------
USER usermgt

# ------------------------------------------------------------
# Expose container port
# ------------------------------------------------------------
EXPOSE 5005

# ------------------------------------------------------------
# Start application via Gunicorn WSGI server
# ------------------------------------------------------------
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:5005", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output", \
     "app:create_app()"]
