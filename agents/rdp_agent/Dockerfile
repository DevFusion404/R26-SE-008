# Build & Run
# ─────────────────────────────────────────────────────────────
# Build:
#   docker build -t rdp-agent .
#
# Run (mount a volume so CodeBERT isn't re-downloaded every restart):
#   docker run -p 5000:5000 \
#     -v hf_cache:/home/rdp/.cache/huggingface \
#     -e HF_TOKEN=your_token_here \
#     rdp-agent
# ─────────────────────────────────────────────────────────────
# Stage 1 — Builder
# Install all dependencies into /install so the final image
# only copies the compiled wheels, not pip caches or build tools.
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /install

# System build deps needed to compile some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        git \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first — lets Docker cache this layer.
# Re-runs only when pyproject.toml changes, not on every code change.
COPY pyproject.toml .

# Install CPU-only PyTorch (saves ~1.5 GB vs the default CUDA build).
# Must be installed before transformers to avoid pulling in CUDA wheels.
RUN pip install --no-cache-dir \
        torch==2.2.2+cpu \
        --index-url https://download.pytorch.org/whl/cpu

# Install all remaining project dependencies
RUN pip install --no-cache-dir \
        flask>=2.0.0 \
        flask-cors>=4.0.0 \
        pyyaml>=6.0 \
        "transformers>=4.0.0,<5.0.0" \
        sentencepiece \
        --prefix=/install/packages

# ─────────────────────────────────────────────────────────────
# Stage 2 — Runtime
# Lean final image: no gcc, no pip cache, no build artifacts.
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user — running containers as root is a security risk
RUN groupadd --gid 1001 rdp && \
    useradd  --uid 1001 --gid rdp --shell /bin/sh --create-home rdp

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install/packages /usr/local

# Copy installed torch (installed outside prefix, so it lands in /usr/local)
COPY --from=builder /usr/local/lib/python3.11/site-packages/torch \
                    /usr/local/lib/python3.11/site-packages/torch

# Copy application source
COPY --chown=rdp:rdp src/         ./src/
COPY --chown=rdp:rdp templates/   ./templates/
COPY --chown=rdp:rdp static/      ./static/
COPY --chown=rdp:rdp app.py       .
COPY --chown=rdp:rdp config.yaml  .

# HuggingFace model cache — CodeBERT (~500 MB) is lazy-loaded on first request.
# Mount a volume here in production so the model persists across restarts
# and is not re-downloaded every time the container starts.
#   docker run -v hf_cache:/home/rdp/.cache/huggingface ...
ENV HF_HOME=/home/rdp/.cache/huggingface

# Tell transformers not to check for updates on every startup (faster cold start)
ENV TRANSFORMERS_OFFLINE=0
ENV TOKENIZERS_PARALLELISM=false

# Flask production settings
ENV FLASK_ENV=production
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER rdp

EXPOSE 5000

# Gunicorn — production-grade WSGI server, not Flask dev server.
# 2 workers is enough for a CPU-bound ML agent; tune with --workers if needed.
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
