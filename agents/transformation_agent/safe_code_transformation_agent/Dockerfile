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

# Copy dependency files
COPY requirements.txt /tmp/requirements.txt
COPY requirements-qwen.txt /tmp/requirements-qwen.txt

# Install all dependencies from requirements.txt
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    -r /tmp/requirements.txt

# Qwen2.5-Coder is SCTVA's primary candidate generator in production. Install the
# CPU PyTorch wheel explicitly so the image remains usable on Azure instances
# without a CUDA runtime, then install the lazy model adapter dependencies.
RUN python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    --index-url https://download.pytorch.org/whl/cpu \
    "torch>=2.4,<3" && \
    PYTHONPATH=/install/packages/lib/python3.11/site-packages \
    python -m pip install --no-cache-dir \
    --prefix=/install/packages \
    -r /tmp/requirements-qwen.txt

# Bundle the checkpoint at image-build time. Production containers therefore
# do not depend on an outbound Hugging Face download during the first request.
ARG SCTVA_QWEN_MODEL_ID=Qwen/Qwen2.5-Coder-0.5B-Instruct
RUN PYTHONPATH=/install/packages/lib/python3.11/site-packages \
    HF_HOME=/install/huggingface \
    python -c "from huggingface_hub import snapshot_download; snapshot_download('${SCTVA_QWEN_MODEL_ID}', allow_patterns=['added_tokens.json','chat_template.jinja','config.json','generation_config.json','merges.txt','model.safetensors','pytorch_model.bin','special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.json'])"


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
COPY --from=builder --chown=sctva:sctva /install/huggingface /home/sctva/.cache/huggingface


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

# The model is attempted first; all candidates still pass SCTVA syntax,
# structural, behavioral and invariant validation. Deterministic rules remain
# the fallback whenever the model output cannot be proven safe.
ENV SCTVA_ENABLE_QWEN=true
ENV SCTVA_QWEN_MODE=primary
ENV SCTVA_QWEN_DEVICE=cpu
ENV SCTVA_QWEN_MODEL_ID=Qwen/Qwen2.5-Coder-0.5B-Instruct
ENV SCTVA_QWEN_MAX_NEW_TOKENS=256
ENV SCTVA_QWEN_CPU_THREADS=4
ENV SCTVA_QWEN_MAX_ACTIONS_PER_REQUEST=100
ENV SCTVA_QWEN_MAX_ACTIONS_PER_FILE=100
ENV SCTVA_QWEN_GENERATION_TIMEOUT_SECONDS=30
ENV SCTVA_QWEN_ALLOW_DOWNLOAD=false
ENV HF_HOME=/home/sctva/.cache/huggingface
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1


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
