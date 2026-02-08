#!/usr/bin/env bash
set -euo pipefail

# MAS Production Deployment Script
# Usage: bash scripts/deploy.sh

COMPOSE_FILE="docker-compose.prod.yml"
PROJECT_DIR="/opt/mas"

echo "=== MAS Deployment ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

cd "${PROJECT_DIR}"

# Pull latest images
echo "--- Pulling images ---"
docker compose -f "${COMPOSE_FILE}" pull

# Run database migrations
echo "--- Running migrations ---"
docker compose -f "${COMPOSE_FILE}" run --rm api \
    alembic upgrade head

# Restart services (rolling)
echo "--- Restarting services ---"
docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans

# Wait for API health
echo "--- Waiting for API health check ---"
MAX_RETRIES=30
RETRY=0
until curl -sf http://localhost:8000/health > /dev/null 2>&1; do
    RETRY=$((RETRY + 1))
    if [ "${RETRY}" -ge "${MAX_RETRIES}" ]; then
        echo "ERROR: API health check failed after ${MAX_RETRIES} retries"
        docker compose -f "${COMPOSE_FILE}" logs api --tail 50
        exit 1
    fi
    echo "  Waiting... (${RETRY}/${MAX_RETRIES})"
    sleep 2
done

echo "--- Health check passed ---"

# Clean up old images
echo "--- Cleaning up old images ---"
docker image prune -f --filter "until=168h"

echo "=== Deployment complete ==="
