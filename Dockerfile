# DispatchGuard - Production Dockerfile
FROM python:3.12-slim

# Prevent Python from writing .pyc files & enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Install system dependencies needed for Pillow and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/

# Create non-root user for security
RUN useradd -m -u 1000 dispatchuser && \
    chown -R dispatchuser:dispatchuser /app

USER dispatchuser

EXPOSE 8000

# Run uvicorn server binding to dynamic Render $PORT
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
