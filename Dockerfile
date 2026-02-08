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
CMD ["sh", "-c", "python -m alembic upgrade head && litestar --app src.api.main:app run --host 0.0.0.0 --port ${PORT:-8000}"]
