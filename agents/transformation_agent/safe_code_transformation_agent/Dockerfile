# ============================================================
# SCTVA - Safe Code Transformation & Validation Agent
# Production Dockerfile
# Python 3.11-slim + Flask + Gunicorn
# ============================================================


# ------------------------------------------------------------
# Stage 1 - Builder
# Install Python dependencies into a separate prefix
# ------------------------------------------------------------
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade \
    pip \
    setuptools \
    wheel

# Copy dependency file
COPY requirements.txt /tmp/requirements.txt

# Install all dependencies from requirements.txt
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    -r /tmp/requirements.txt


# ------------------------------------------------------------
# Stage 2 - Runtime
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


# ------------------------------------------------------------
# Application working directory
# ------------------------------------------------------------
WORKDIR /app


# ------------------------------------------------------------
# Copy installed Python packages from builder stage
# ------------------------------------------------------------
COPY --from=builder /install/packages /usr/local


# ------------------------------------------------------------
# Copy application source
# ------------------------------------------------------------
COPY --chown=sctva:sctva sctva/ ./sctva/
COPY --chown=sctva:sctva static/ ./static/
COPY --chown=sctva:sctva app.py ./


# ------------------------------------------------------------
# Python / Flask configuration
# ------------------------------------------------------------
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

ENV FLASK_ENV=production
ENV FLASK_APP=app.py

ENV SCTVA_PORT=8002
ENV PORT=8002


# ------------------------------------------------------------
# Runtime directory
# ------------------------------------------------------------
RUN mkdir -p /app/.sctva_runtime && \
    chown -R sctva:sctva /app/.sctva_runtime


# ------------------------------------------------------------
# Switch to non-root user
# ------------------------------------------------------------
USER sctva


# ------------------------------------------------------------
# Expose SCTVA port
# ------------------------------------------------------------
EXPOSE 8002


# ------------------------------------------------------------
# Start application using Gunicorn
# ------------------------------------------------------------
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:8002", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "3000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output", \
     "app:create_app()"]