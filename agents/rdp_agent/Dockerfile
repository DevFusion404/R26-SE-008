# ============================================================
# RDP Agent - Azure Container Apps Dockerfile
# Python 3.11 + Flask + Gunicorn + CodeBERT / PyTorch CPU
# ============================================================


# ------------------------------------------------------------
# Stage 1 - Builder
# ------------------------------------------------------------
FROM python:3.11-slim AS builder

# Build dependencies required by some Python packages
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


# ------------------------------------------------------------
# Install CPU-only PyTorch
#
# Install everything under /install/packages so that all Python
# packages and their dependencies can be copied cleanly into
# the runtime image.
# ------------------------------------------------------------
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    "torch==2.2.2+cpu" \
    --extra-index-url https://download.pytorch.org/whl/cpu


# ------------------------------------------------------------
# Install RDP dependencies
#
# IMPORTANT:
# Gunicorn is included because the container CMD uses:
#
#     python -m gunicorn
#
# Without Gunicorn the container exits with code 1.
# ------------------------------------------------------------
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    "flask>=2.0.0" \
    "flask-cors>=4.0.0" \
    "pyyaml>=6.0" \
    "transformers>=4.0.0,<5.0.0" \
    "sentencepiece>=0.1.99" \
    "gunicorn>=21.0.0"


# ------------------------------------------------------------
# Stage 2 - Runtime
# ------------------------------------------------------------
FROM python:3.11-slim AS runtime


# ------------------------------------------------------------
# Create non-root application user
# ------------------------------------------------------------
RUN groupadd --gid 1001 rdp && \
    useradd \
        --uid 1001 \
        --gid 1001 \
        --shell /bin/sh \
        --create-home \
        rdp


# Application directory
WORKDIR /app


# ------------------------------------------------------------
# Copy installed Python packages from builder
# ------------------------------------------------------------
COPY --from=builder /install/packages /usr/local


# ------------------------------------------------------------
# Copy application source
# ------------------------------------------------------------
COPY --chown=rdp:rdp src/ ./src/
COPY --chown=rdp:rdp templates/ ./templates/
COPY --chown=rdp:rdp static/ ./static/

COPY --chown=rdp:rdp app.py ./
COPY --chown=rdp:rdp config.yaml ./


# ------------------------------------------------------------
# Hugging Face / Transformers configuration
# ------------------------------------------------------------

# CodeBERT model cache
ENV HF_HOME=/home/rdp/.cache/huggingface

# Allow downloading CodeBERT when it is not already cached
ENV TRANSFORMERS_OFFLINE=0

# Avoid unnecessary tokenizer parallel worker warnings
ENV TOKENIZERS_PARALLELISM=false


# ------------------------------------------------------------
# Python / Flask configuration
# ------------------------------------------------------------
ENV FLASK_ENV=production
ENV FLASK_APP=app.py

# Immediately send Python logs to stdout/stderr.
# Very useful for Azure Container Apps logs.
ENV PYTHONUNBUFFERED=1

# Avoid creating .pyc files
ENV PYTHONDONTWRITEBYTECODE=1

# Application port
ENV PORT=5000


# ------------------------------------------------------------
# Prepare Hugging Face cache
#
# Explicitly create this folder and assign ownership so the
# non-root rdp user can download/cache CodeBERT.
# ------------------------------------------------------------
RUN mkdir -p /home/rdp/.cache/huggingface && \
    chown -R rdp:rdp /home/rdp/.cache


# ------------------------------------------------------------
# Run as non-root user
# ------------------------------------------------------------
USER rdp


# ------------------------------------------------------------
# Azure Container Apps target port
# ------------------------------------------------------------
EXPOSE 5000


# ------------------------------------------------------------
# Start Flask through Gunicorn
#
# One worker is intentional.
# RDP loads CodeBERT/PyTorch and multiple Gunicorn workers can
# duplicate model memory.
#
# Azure ingress target port = 5000
# ------------------------------------------------------------
CMD ["python", "-m", "gunicorn", \
    "--bind", "0.0.0.0:5000", \
    "--workers", "1", \
    "--threads", "2", \
    "--timeout", "180", \
    "--access-logfile", "-", \
    "--error-logfile", "-", \
    "--capture-output", \
    "app:app"]