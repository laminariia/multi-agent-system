#!/usr/bin/env bash
set -euo pipefail

# MAS PostgreSQL Backup Script
# Usage: bash scripts/backup_postgres.sh [--dir /path/to/backups]
#
# Creates a timestamped pg_dump backup of the MAS database.
# Reads connection info from DATABASE_URL or uses defaults.
#
# Requirements: pg_dump (from postgresql-client package)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKUP_DIR="${HOME}/mas-backups"
RETENTION_DAYS=30

# Parse optional args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            BACKUP_DIR="$2"
            shift 2
            ;;
        --retention)
            RETENTION_DAYS="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: bash scripts/backup_postgres.sh [--dir /path] [--retention days]"
            exit 1
            ;;
    esac
done

# Parse DATABASE_URL or use defaults
DATABASE_URL="${DATABASE_URL:-postgresql://mas:mas_password@localhost:5432/mas}"

# Extract components from DATABASE_URL
# Format: postgresql://user:password@host:port/dbname
DB_USER=$(echo "${DATABASE_URL}" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "${DATABASE_URL}" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo "${DATABASE_URL}" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "${DATABASE_URL}" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "${DATABASE_URL}" | sed -n 's|.*/\([^?]*\).*|\1|p')

TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/mas_backup_${TIMESTAMP}.sql.gz"

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

echo "=== MAS PostgreSQL Backup ==="
echo "Date:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Database:  ${DB_NAME}@${DB_HOST}:${DB_PORT}"
echo "Backup to: ${BACKUP_FILE}"
echo ""

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

# Run pg_dump with compression
echo "--- Creating backup ---"
export PGPASSWORD="${DB_PASS}"

pg_dump \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    --format=custom \
    --compress=9 \
    --verbose \
    --no-owner \
    --no-privileges \
    --file="${BACKUP_DIR}/mas_backup_${TIMESTAMP}.dump" \
    2>&1 | tail -5

# Also create a plain SQL backup (gzipped) for portability
pg_dump \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    --format=plain \
    --no-owner \
    --no-privileges \
    | gzip > "${BACKUP_FILE}"

unset PGPASSWORD

# Verify backup file exists and has content
DUMP_SIZE=$(stat -c%s "${BACKUP_DIR}/mas_backup_${TIMESTAMP}.dump" 2>/dev/null || stat -f%z "${BACKUP_DIR}/mas_backup_${TIMESTAMP}.dump" 2>/dev/null || echo "0")
SQL_SIZE=$(stat -c%s "${BACKUP_FILE}" 2>/dev/null || stat -f%z "${BACKUP_FILE}" 2>/dev/null || echo "0")

echo ""
echo "--- Backup complete ---"
echo "  Custom format: mas_backup_${TIMESTAMP}.dump (${DUMP_SIZE} bytes)"
echo "  SQL (gzipped): mas_backup_${TIMESTAMP}.sql.gz (${SQL_SIZE} bytes)"

# ---------------------------------------------------------------------------
# Cleanup old backups
# ---------------------------------------------------------------------------

echo ""
echo "--- Cleaning up backups older than ${RETENTION_DAYS} days ---"
DELETED=$(find "${BACKUP_DIR}" -name "mas_backup_*" -mtime +"${RETENTION_DAYS}" -delete -print 2>/dev/null | wc -l)
echo "  Deleted ${DELETED} old backup files"

# List remaining backups
echo ""
echo "--- Current backups ---"
ls -lh "${BACKUP_DIR}"/mas_backup_* 2>/dev/null || echo "  (none)"

echo ""
echo "=== Backup complete ==="
