#!/usr/bin/env bash
set -euo pipefail

# MAS PostgreSQL Restore Script
# Usage: bash scripts/restore_postgres.sh <backup_file>
#
# Restores a MAS database backup created by backup_postgres.sh.
# Supports both custom format (.dump) and plain SQL (.sql.gz) backups.
#
# Requirements: pg_restore / psql (from postgresql-client package)

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
    echo "Usage: bash scripts/restore_postgres.sh <backup_file> [--yes]"
    echo ""
    echo "Supported formats:"
    echo "  .dump    — pg_dump custom format (preferred)"
    echo "  .sql.gz  — gzipped plain SQL"
    echo "  .sql     — plain SQL"
    echo ""
    echo "Options:"
    echo "  --yes    — skip confirmation prompt"
    echo ""
    echo "Available backups:"
    BACKUP_DIR="${HOME}/mas-backups"
    ls -lh "${BACKUP_DIR}"/mas_backup_* 2>/dev/null || echo "  (none found in ${BACKUP_DIR})"
    exit 1
fi

BACKUP_FILE="$1"
SKIP_CONFIRM=false

if [[ "${2:-}" == "--yes" ]]; then
    SKIP_CONFIRM=true
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "ERROR: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL="${DATABASE_URL:-postgresql://mas:mas_password@localhost:5432/mas}"

DB_USER=$(echo "${DATABASE_URL}" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "${DATABASE_URL}" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo "${DATABASE_URL}" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "${DATABASE_URL}" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "${DATABASE_URL}" | sed -n 's|.*/\([^?]*\).*|\1|p')

echo "=== MAS PostgreSQL Restore ==="
echo "Date:       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Database:   ${DB_NAME}@${DB_HOST}:${DB_PORT}"
echo "Backup:     ${BACKUP_FILE}"
echo ""

# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

if [[ "${SKIP_CONFIRM}" != true ]]; then
    echo "WARNING: This will DROP and recreate all tables in '${DB_NAME}'."
    echo "         All existing data will be LOST."
    echo ""
    read -rp "Type 'yes' to continue: " CONFIRM
    if [[ "${CONFIRM}" != "yes" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

export PGPASSWORD="${DB_PASS}"

# ---------------------------------------------------------------------------
# Pre-restore: terminate active connections
# ---------------------------------------------------------------------------

echo ""
echo "--- Terminating active connections to ${DB_NAME} ---"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
" 2>/dev/null || echo "  (no active connections or insufficient privileges)"

# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

echo ""
echo "--- Restoring from backup ---"

case "${BACKUP_FILE}" in
    *.dump)
        # Custom format — use pg_restore with --clean to drop existing objects
        pg_restore \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${DB_NAME}" \
            --clean \
            --if-exists \
            --no-owner \
            --no-privileges \
            --verbose \
            "${BACKUP_FILE}" \
            2>&1 | tail -10
        ;;
    *.sql.gz)
        # Gzipped plain SQL
        gunzip -c "${BACKUP_FILE}" | psql \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${DB_NAME}" \
            --quiet \
            --set ON_ERROR_STOP=1
        ;;
    *.sql)
        # Plain SQL
        psql \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${DB_NAME}" \
            --quiet \
            --set ON_ERROR_STOP=1 \
            -f "${BACKUP_FILE}"
        ;;
    *)
        echo "ERROR: Unsupported backup format. Expected .dump, .sql.gz, or .sql"
        exit 1
        ;;
esac

unset PGPASSWORD

# ---------------------------------------------------------------------------
# Post-restore: verify
# ---------------------------------------------------------------------------

echo ""
echo "--- Verifying restore ---"

export PGPASSWORD="${DB_PASS}"

TABLE_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';
" 2>/dev/null || echo "?")

ALEMBIC_VER=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT version_num FROM alembic_version LIMIT 1;
" 2>/dev/null || echo "?")

unset PGPASSWORD

echo "  Tables restored: ${TABLE_COUNT}"
echo "  Alembic version: ${ALEMBIC_VER}"

# Check if we need to run pending migrations
echo ""
echo "--- Checking for pending migrations ---"
if python -m alembic check 2>/dev/null; then
    echo "  No pending migrations. Database is up to date."
else
    echo "  Pending migrations detected. Run: python -m alembic upgrade head"
fi

echo ""
echo "=== Restore complete ==="
