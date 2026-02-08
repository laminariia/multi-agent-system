FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by asyncpg and cryptography
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from pyproject.toml
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Copy application source
COPY src/ ./src/

# Copy Alembic migration files
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Run migrations then start the server
# Retry alembic up to 5 times (PostgreSQL may not be ready immediately)
CMD ["sh", "-c", "for i in 1 2 3 4 5; do python -m alembic upgrade head && break || echo \"Alembic attempt $i failed, retrying in 5s...\" && sleep 5; done && litestar --app src.api.main:app run --host 0.0.0.0 --port ${PORT:-8000}"]
