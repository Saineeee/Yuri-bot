# syntax=docker/dockerfile:1
# Yuri-bot Dockerfile
# Multi-stage build for a smaller final image.

# ---- Builder stage ----
FROM python:3.11-slim AS builder

# System deps for Pillow + voice (ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Runtime stage ----
FROM python:3.11-slim AS runtime

# ffmpeg for voice support (optional but included)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy application code
COPY . .

# Run as a non-root user for security
RUN useradd -m -u 1000 yuri && chown -R yuri:yuri /app
USER yuri

# Health check: fails if DISCORD_TOKEN is missing
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0 if os.getenv('DISCORD_TOKEN') else 1)"

CMD ["python", "main.py"]
