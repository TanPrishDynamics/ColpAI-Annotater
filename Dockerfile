# ---- ColpAI Annotation Platform — Cloud Run container ----
# Multi-stage build: slim Python image, no dev tools in prod.

FROM python:3.12-slim AS base

# Prevent Python from buffering stdout/stderr (important for Cloud Run logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps required by Pillow / psycopg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libjpeg62-turbo-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# ---- Dependencies layer (cached unless requirements change) ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn>=21.2

# ---- Application code ----
COPY . .

# Create the data directory (SQLite DB, uploads)
RUN mkdir -p data

# Cloud Run injects $PORT; gunicorn binds to it.
# Default to 8080 for local docker testing.
ENV PORT=8080

# Run gunicorn with the Cloud Run-friendly settings.
# Cloud Run sends SIGTERM for graceful shutdown.
CMD exec gunicorn \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --threads 4 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    wsgi:app
