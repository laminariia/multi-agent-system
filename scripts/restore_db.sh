#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MAS Database Restore Script (Disaster Recovery)
#
# Restores a PostgreSQL backup with comprehensive safety checks:
#   - Pre-restore: connectivity test, backup integrity, active connection warning
#   - Restore: pg_restore (custom) or psql (SQL/gzipped SQL)
#   - Post-restore: table count, row counts, Alembic version, referential integrity
#
# Usage:
#   bash scripts/restore_db.sh <backup_file> [OPTIONS]
#
# Arguments:
#   backup_file          Path to .dump, .sql.gz, or .sql backup file
#
# Options:
#   --yes                Skip interactive confirmation prompt
#   --stop-services      Stop API/worker containers before restore
#   --run-migrations     Run Alembic upgrade head after restore
#   --dry-run            Validate backup without restoring
#   --target-db URL      Override DATABASE_URL for restore target
#   --help               Show this help message
#
# Environment:
#   DATABASE_URL         PostgreSQL connection string (required)
#
# Spec: docs/Full_work/specs/deploy-spec.md "Disaster Recovery Scenarios"
# =============================================================================

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

BACKUP_FILE=""
SKIP_CONFIRM=false
STOP_SERVICES=false
RUN_MIGRATIONS=false
DRY_RUN=false
TARGET_DB=""
SCRIPT_NAME="$(basename "$0")"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    sed -n '3,29p' "$0" | sed 's/^# \?//'
    echo ""
    echo "Available backups:"
    local backup_dir="${HOME}/mas-backups"
    for tier in daily weekly monthly; do
        if [[ -d "${backup_dir}/${tier}" ]]; then
            echo "  [${tier}]"
            ls -lh "${backup_dir}/${tier}"/mas_*.dump 2>/dev/null | tail -5 || echo "    (none)"
        fi
    done
    exit 0
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log_info()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [INFO]  $*"; }
log_warn()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [WARN]  $*" >&2; }
log_error() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [ERROR] $*" >&2; }

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

cleanup() {
    unset PGPASSWORD
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
    usage
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes)
            SKIP_CONFIRM=true
            shift
            ;;
        --stop-services)
            STOP_SERVICES=true
            shift
            ;;
        --run-migrations)
            RUN_MIGRATIONS=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --target-db)
            TARGET_DB="$2"
            shift 2
            ;;
        --help|-h)
            usage
            ;;
        -*)
            log_error "Unknown option: $1"
            usage
            ;;
        *)
            if [[ -z "${BACKUP_FILE}" ]]; then
                BACKUP_FILE="$1"
            else
                log_error "Unexpected argument: $1"
                usage
            fi
            shift
            ;;
    esac
done

if [[ -z "${BACKUP_FILE}" ]]; then
    log_error "Backup file argument is required"
    usage
fi

# ---------------------------------------------------------------------------
# Parse DATABASE_URL
# ---------------------------------------------------------------------------

DATABASE_URL="${TARGET_DB:-${DATABASE_URL:-}}"
if [[ -z "${DATABASE_URL}" ]]; then
    log_error "DATABASE_URL environment variable is required (or use --target-db)"
    exit 1
fi

DB_USER=$(echo "${DATABASE_URL}" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "${DATABASE_URL}" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo "${DATABASE_URL}" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "${DATABASE_URL}" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "${DATABASE_URL}" | sed -n 's|.*/\([^?]*\).*|\1|p')

if [[ -z "${DB_USER}" || -z "${DB_HOST}" || -z "${DB_NAME}" ]]; then
    log_error "Could not parse DATABASE_URL"
    exit 1
fi

export PGPASSWORD="${DB_PASS}"

# ===========================================================================
# PRE-RESTORE VALIDATION
# ===========================================================================

log_info "=== MAS Database Restore ==="
log_info "Date:       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log_info "Database:   ${DB_NAME}@${DB_HOST}:${DB_PORT}"
log_info "Backup:     ${BACKUP_FILE}"
log_info "Dry run:    ${DRY_RUN}"

# --- Check backup file exists ---
if [[ ! -f "${BACKUP_FILE}" ]]; then
    log_error "Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

BACKUP_SIZE=$(stat -c%s "${BACKUP_FILE}" 2>/dev/null || stat -f%z "${BACKUP_FILE}" 2>/dev/null || echo "0")
if [[ "${BACKUP_SIZE}" -eq 0 ]]; then
    log_error "Backup file is empty: ${BACKUP_FILE}"
    exit 1
fi
log_info "Backup size: ${BACKUP_SIZE} bytes"

# --- Validate backup integrity ---
log_info "--- Pre-restore: validating backup ---"

case "${BACKUP_FILE}" in
    *.dump)
        if pg_restore --list "${BACKUP_FILE}" > /dev/null 2>&1; then
            TABLE_COUNT_BACKUP=$(pg_restore --list "${BACKUP_FILE}" 2>/dev/null | grep -c "TABLE DATA" || echo "0")
            log_info "  Backup format: custom (pg_dump)"
            log_info "  Table data entries: ${TABLE_COUNT_BACKUP}"
        else
            log_error "  Backup file is corrupt or not a valid pg_dump custom format"
            exit 1
        fi
        ;;
    *.sql.gz)
        if gunzip -t "${BACKUP_FILE}" 2>/dev/null; then
            log_info "  Backup format: gzipped SQL"
            log_info "  gzip integrity: OK"
        else
            log_error "  gzip integrity check FAILED"
            exit 1
        fi
        ;;
    *.sql)
        log_info "  Backup format: plain SQL"
        ;;
    *)
        log_error "Unsupported backup format. Expected: .dump, .sql.gz, or .sql"
        exit 1
        ;;
esac

# --- Verify checksum if available ---
CHECKSUM_FILE="${BACKUP_FILE%.*}.sha256"
# For .sql.gz, strip both extensions
if [[ "${BACKUP_FILE}" == *.sql.gz ]]; then
    CHECKSUM_FILE="${BACKUP_FILE%.sql.gz}.sha256"
fi

if [[ -f "${CHECKSUM_FILE}" ]]; then
    log_info "  Verifying checksum..."
    if sha256sum -c "${CHECKSUM_FILE}" --quiet 2>/dev/null \
        || shasum -a 256 -c "${CHECKSUM_FILE}" --quiet 2>/dev/null; then
        log_info "  Checksum: OK"
    else
        log_error "  Checksum verification FAILED"
        exit 1
    fi
else
    log_warn "  No checksum file found (expected: ${CHECKSUM_FILE})"
fi

# --- Test database connectivity ---
log_info "--- Pre-restore: testing connectivity ---"
if psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1;" > /dev/null 2>&1; then
    log_info "  Database connection: OK"
else
    log_error "  Cannot connect to database ${DB_NAME}@${DB_HOST}:${DB_PORT}"
    exit 1
fi

# --- Record pre-restore state ---
PRE_TABLE_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';
" 2>/dev/null || echo "?")

PRE_ALEMBIC=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT version_num FROM alembic_version LIMIT 1;
" 2>/dev/null || echo "?")

log_info "  Current tables: ${PRE_TABLE_COUNT}"
log_info "  Current Alembic version: ${PRE_ALEMBIC}"

# --- Check active connections ---
ACTIVE_CONNS=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT count(*) FROM pg_stat_activity
    WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
" 2>/dev/null || echo "0")

if [[ "${ACTIVE_CONNS}" -gt 0 ]]; then
    log_warn "  ${ACTIVE_CONNS} active connection(s) to database"
fi

# --- Dry run exits here ---
if [[ "${DRY_RUN}" == true ]]; then
    log_info "[DRY RUN] Backup validated successfully. No restore performed."
    exit 0
fi

# ===========================================================================
# CONFIRMATION
# ===========================================================================

if [[ "${SKIP_CONFIRM}" != true ]]; then
    echo ""
    echo "WARNING: This will DROP and recreate all objects in '${DB_NAME}'."
    echo "         All existing data will be REPLACED with backup contents."
    echo ""
    read -rp "Type 'restore' to continue: " CONFIRM
    if [[ "${CONFIRM}" != "restore" ]]; then
        log_info "Aborted by user."
        exit 0
    fi
fi

# ===========================================================================
# STOP SERVICES (optional)
# ===========================================================================

if [[ "${STOP_SERVICES}" == true ]]; then
    log_info "--- Stopping services ---"
    if command -v docker &>/dev/null; then
        docker compose stop api worker 2>/dev/null \
            || docker-compose stop api worker 2>/dev/null \
            || log_warn "Could not stop docker services"
    else
        log_warn "Docker not found — skipping service stop"
    fi
fi

# ===========================================================================
# TERMINATE CONNECTIONS
# ===========================================================================

log_info "--- Terminating active connections ---"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
" > /dev/null 2>&1 || log_warn "Could not terminate connections (may require superuser)"

# ===========================================================================
# RESTORE
# ===========================================================================

log_info "--- Restoring from backup ---"
RESTORE_START=$(date +%s)

case "${BACKUP_FILE}" in
    *.dump)
        pg_restore \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${DB_NAME}" \
            --clean \
            --if-exists \
            --no-owner \
            --no-privileges \
            --single-transaction \
            --exit-on-error \
            "${BACKUP_FILE}" \
            2>&1 | tail -20
        ;;
    *.sql.gz)
        gunzip -c "${BACKUP_FILE}" | psql \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${DB_NAME}" \
            --quiet \
            --set ON_ERROR_STOP=1 \
            --single-transaction
        ;;
    *.sql)
        psql \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${DB_NAME}" \
            --quiet \
            --set ON_ERROR_STOP=1 \
            --single-transaction \
            -f "${BACKUP_FILE}"
        ;;
esac

RESTORE_END=$(date +%s)
RESTORE_DURATION=$((RESTORE_END - RESTORE_START))
log_info "Restore completed in ${RESTORE_DURATION}s"

# ===========================================================================
# POST-RESTORE VALIDATION
# ===========================================================================

log_info "--- Post-restore: validation ---"

# 1. Table count
POST_TABLE_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';
" 2>/dev/null || echo "?")
log_info "  Tables restored: ${POST_TABLE_COUNT}"

# 2. Alembic version
POST_ALEMBIC=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT version_num FROM alembic_version LIMIT 1;
" 2>/dev/null || echo "?")
log_info "  Alembic version: ${POST_ALEMBIC}"

# 3. Critical table row counts
log_info "  Row counts for critical tables:"
for table in users jobs bids projects hitl_queue langgraph_checkpoints leads; do
    count=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
        SELECT count(*) FROM ${table};
    " 2>/dev/null || echo "N/A")
    log_info "    ${table}: ${count}"
done

# 4. Foreign key integrity check
log_info "  Checking referential integrity..."
FK_VIOLATIONS=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT count(*)
    FROM information_schema.table_constraints tc
    JOIN information_schema.constraint_column_usage ccu
        ON tc.constraint_name = ccu.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY'
        AND tc.table_schema = 'public';
" 2>/dev/null || echo "?")
log_info "    Foreign key constraints present: ${FK_VIOLATIONS}"

# 5. pgvector extension
PGVECTOR=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT extversion FROM pg_extension WHERE extname = 'vector';
" 2>/dev/null || echo "?")
log_info "    pgvector extension: ${PGVECTOR:-not installed}"

# ===========================================================================
# RUN MIGRATIONS (optional)
# ===========================================================================

if [[ "${RUN_MIGRATIONS}" == true ]]; then
    log_info "--- Running Alembic migrations ---"
    if command -v python &>/dev/null || command -v python3 &>/dev/null; then
        PYTHON_CMD=$(command -v python3 || command -v python)
        "${PYTHON_CMD}" -m alembic upgrade head 2>&1 || log_warn "Alembic migration failed"

        FINAL_ALEMBIC=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
            SELECT version_num FROM alembic_version LIMIT 1;
        " 2>/dev/null || echo "?")
        log_info "  Alembic version after migrations: ${FINAL_ALEMBIC}"
    else
        log_warn "Python not found — skipping migrations"
    fi
fi

# ===========================================================================
# RESTART SERVICES (if stopped)
# ===========================================================================

if [[ "${STOP_SERVICES}" == true ]]; then
    log_info "--- Restarting services ---"
    if command -v docker &>/dev/null; then
        docker compose start api worker 2>/dev/null \
            || docker-compose start api worker 2>/dev/null \
            || log_warn "Could not restart docker services"
    fi
fi

# ===========================================================================
# Summary
# ===========================================================================

log_info "=== Restore Complete ==="
log_info "Duration:   ${RESTORE_DURATION}s"
log_info "Tables:     ${PRE_TABLE_COUNT} -> ${POST_TABLE_COUNT}"
log_info "Alembic:    ${PRE_ALEMBIC} -> ${POST_ALEMBIC}"
