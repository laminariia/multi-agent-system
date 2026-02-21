# ============================================================
# Stage 1: Builder — install Python dependencies
# ============================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# System deps for building wheels (asyncpg, cryptography)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .


# ============================================================
# Stage 2: Runtime — minimal production image
# ============================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install runtime libraries and Playwright system deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl wget \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=appuser:appuser src/ ./src/

# Copy Alembic migration files
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic/ ./alembic/

# Install Playwright Chromium browser with system dependencies
RUN playwright install --with-deps chromium

# Set proper file permissions
RUN chmod -R 555 /app/src && chmod -R 755 /app/alembic

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["sh", "-c", "curl -sf http://localhost:${PORT:-8000}/health || exit 1"]

# Switch to non-root user
USER appuser

# Run migrations then start the server
CMD ["sh", "-c", "for i in 1 2 3 4 5; do python -m alembic upgrade head && break || echo \"Alembic attempt $i failed, retrying in 5s...\" && sleep 5; done && litestar --app src.api.main:app run --host 0.0.0.0 --port ${PORT:-8000}"]
