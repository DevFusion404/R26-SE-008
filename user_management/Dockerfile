# Build & Run Instructions
# -------------------------------------------------------------
# Build:
#   docker build -t user-management-service .
#
# Run:
#   docker run -p 6000:6000 \
#     --env-file .env \
#     user-management-service
# -------------------------------------------------------------

# Stage 1 — Builder
FROM python:3.11-slim AS builder

WORKDIR /install

# Copy requirements file first to utilize build caching
COPY requirements.txt .

# Install dependencies under the prefix path /install/packages
RUN pip install --no-cache-dir --prefix=/install/packages -r requirements.txt


# -------------------------------------------------------------
# Stage 2 — Runtime
# Copy over dependencies and application code.
# -------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Create a non-root user for security
RUN groupadd --gid 1002 usermgt && \
    useradd  --uid 1002 --gid usermgt --shell /bin/sh --create-home usermgt

WORKDIR /app

# Copy python packages installed in the builder stage
COPY --from=builder /install/packages /usr/local

# Copy application files
COPY --chown=usermgt:usermgt app.py .
COPY --chown=usermgt:usermgt config/ ./config/
COPY --chown=usermgt:usermgt routes/ ./routes/
COPY --chown=usermgt:usermgt services/ ./services/
COPY --chown=usermgt:usermgt middleware/ ./middleware/
COPY --chown=usermgt:usermgt models/ ./models/

# Production environment variables
ENV FLASK_ENV=production
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose the API port
EXPOSE 6000

# Switch to the non-root user
USER usermgt

# Serve the application using gunicorn
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:6000", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
