FROM python:3.12-slim

# Labels for Unraid CA
LABEL maintainer="greateye"
LABEL org.opencontainers.image.title="Great Eye"
LABEL org.opencontainers.image.description="Unified homelab operations dashboard"

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Create app user
RUN groupadd -g 1000 greateye && \
    useradd -u 1000 -g greateye -m greateye

# App directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY static/ ./static/
COPY templates/ ./templates/
COPY alembic/ ./alembic/
COPY alembic.ini .

# Default data directory
RUN mkdir -p /config && chown greateye:greateye /config

# Runtime config
ENV DATABASE_URL=sqlite+aiosqlite:///config/greateye.db
ENV PYTHONUNBUFFERED=1

# PUID/PGID support (handled by entrypoint)
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8484/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8484"]
