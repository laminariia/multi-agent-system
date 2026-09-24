#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MAS Disaster Recovery Test Script
#
# Validates backup integrity by restoring to a temporary database and running
# comprehensive checks. Cleans up after itself — safe to run in production.
#
# Tests performed:
#   1. Backup file integrity (gzip/pg_restore)
#   2. Restore to temp database
#   3. Table count validation (minimum 10 expected)
#   4. Critical table row counts (users, jobs, bids, projects, etc.)
#   5. Alembic migration version check
#   6. pgvector extension availability
#   7. Foreign key constraint presence
#   8. Timing measurement (RTO estimation)
#
# Usage:
#   bash scripts/dr_test.sh <backup_file> [OPTIONS]
#
# Arguments:
#   backup_file          Path to .dump, .sql.gz, or .sql backup file
#
# Options:
#   --keep-db            Do NOT drop the temp database after test (for inspection)
#   --min-tables N       Minimum expected table count (default: 10)
#   --telegram           Send Telegram notification with results
#   --verbose            Show detailed psql output during restore
#   --dry-run            Validate backup file without restoring
#   --help               Show this help message
#
# Environment:
#   DATABASE_URL         PostgreSQL connection string (required)
#                        Used for admin connection (create/drop temp DB)
#   TELEGRAM_BOT_TOKEN   Token for result notification (optional)
#   TELEGRAM_CHAT_ID     Chat ID for result notification (optional)
#
# Recovery testing schedule (from deploy-spec.md):
#   Database restore:     monthly
#   Valkey restore:       quarterly
#   Full DR simulation:   annually
#
# Spec: docs/Full_work/specs/deploy-spec.md "Recovery Testing Schedule"
# =============================================================================

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

BACKUP_FILE=""
KEEP_DB=false
MIN_TABLES=10
TELEGRAM_NOTIFY=false
VERBOSE=false
DRY_RUN=false
SCRIPT_NAME="$(basename "$0")"
TEST_DB_PREFIX="mas_dr_test"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    sed -n '3,40p' "$0" | sed 's/^# \?//'
    exit 0
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log_info()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [INFO]  $*"; }
log_warn()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [WARN]  $*" >&2; }
log_error() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [ERROR] $*" >&2; }
log_pass()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [PASS]  $*"; }
log_fail()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [FAIL]  $*" >&2; }

# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

send_telegram_alert() {
    local message="$1"
    if [[ "${TELEGRAM_NOTIFY}" == true ]] \
        && [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] \
        && [[ -n "${TELEGRAM_CHAT_ID:-}" ]]; then
        curl -sf -X POST \
            "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="${message}" \
            -d parse_mode="HTML" \
            > /dev/null 2>&1 || log_warn "Telegram notification failed"
    fi
}

# ---------------------------------------------------------------------------
# Cleanup handler
# ---------------------------------------------------------------------------

TEST_DB_NAME=""
CLEANUP_NEEDED=false

cleanup() {
    local exit_code=$?
    unset PGPASSWORD

    if [[ "${CLEANUP_NEEDED}" == true ]] && [[ "${KEEP_DB}" != true ]] && [[ -n "${TEST_DB_NAME}" ]]; then
        log_info "Cleaning up temp database: ${TEST_DB_NAME}"
        export PGPASSWORD="${DB_PASS}"

        # Terminate any lingering connections to the temp DB
        psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres -c "
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = '${TEST_DB_NAME}' AND pid <> pg_backend_pid();
        " > /dev/null 2>&1 || true

        psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres -c \
            "DROP DATABASE IF EXISTS ${TEST_DB_NAME};" > /dev/null 2>&1 \
            || log_warn "Could not drop temp database ${TEST_DB_NAME} (manual cleanup required)"

        unset PGPASSWORD
    fi

    if [[ ${exit_code} -ne 0 ]]; then
        log_error "DR test ended with exit code ${exit_code}"
    fi
    exit ${exit_code}
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
        --keep-db)
            KEEP_DB=true
            shift
            ;;
        --min-tables)
            MIN_TABLES="$2"
            shift 2
            ;;
        --telegram)
            TELEGRAM_NOTIFY=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
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

DATABASE_URL="${DATABASE_URL:-}"
if [[ -z "${DATABASE_URL}" ]]; then
    log_error "DATABASE_URL environment variable is required"
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

# Generate unique temp DB name using epoch seconds
TEST_DB_NAME="${TEST_DB_PREFIX}_$(date +%s)"

# Build the connection URL for the temp database
TEST_DB_URL=$(echo "${DATABASE_URL}" | sed "s|/${DB_NAME}|/${TEST_DB_NAME}|")

# ===========================================================================
# PRE-FLIGHT CHECKS
# ===========================================================================

log_info "=== MAS Disaster Recovery Test ==="
log_info "Date:       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log_info "Backup:     ${BACKUP_FILE}"
log_info "Source DB:  ${DB_NAME}@${DB_HOST}:${DB_PORT}"
log_info "Temp DB:    ${TEST_DB_NAME}"
log_info "Min tables: ${MIN_TABLES}"
log_info "Keep DB:    ${KEEP_DB}"
log_info "Dry run:    ${DRY_RUN}"

# --- Check backup file ---
if [[ ! -f "${BACKUP_FILE}" ]]; then
    log_error "Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

BACKUP_SIZE=$(stat -c%s "${BACKUP_FILE}" 2>/dev/null || stat -f%z "${BACKUP_FILE}" 2>/dev/null || echo "0")
if [[ "${BACKUP_SIZE}" -eq 0 ]]; then
    log_error "Backup file is empty"
    exit 1
fi
log_info "Backup size: ${BACKUP_SIZE} bytes"

# --- Validate backup integrity ---
log_info "--- Step 1: Validate backup integrity ---"

case "${BACKUP_FILE}" in
    *.dump)
        if pg_restore --list "${BACKUP_FILE}" > /dev/null 2>&1; then
            BACKUP_TABLE_DATA=$(pg_restore --list "${BACKUP_FILE}" 2>/dev/null | grep -c "TABLE DATA" || echo "0")
            log_pass "Custom format valid (${BACKUP_TABLE_DATA} table data entries)"
        else
            log_fail "Backup file is corrupt or not valid pg_dump custom format"
            exit 1
        fi
        ;;
    *.sql.gz)
        if gunzip -t "${BACKUP_FILE}" 2>/dev/null; then
            log_pass "gzip integrity OK"
        else
            log_fail "gzip integrity check FAILED"
            exit 1
        fi
        ;;
    *.sql)
        log_pass "Plain SQL format (no integrity check needed)"
        ;;
    *)
        log_fail "Unsupported format. Expected: .dump, .sql.gz, or .sql"
        exit 1
        ;;
esac

# --- Verify checksum if available ---
CHECKSUM_FILE="${BACKUP_FILE%.*}.sha256"
if [[ "${BACKUP_FILE}" == *.sql.gz ]]; then
    CHECKSUM_FILE="${BACKUP_FILE%.sql.gz}.sha256"
fi

if [[ -f "${CHECKSUM_FILE}" ]]; then
    if sha256sum -c "${CHECKSUM_FILE}" --quiet 2>/dev/null \
        || shasum -a 256 -c "${CHECKSUM_FILE}" --quiet 2>/dev/null; then
        log_pass "Checksum verification OK"
    else
        log_fail "Checksum verification FAILED"
        exit 1
    fi
else
    log_warn "No checksum file found (expected: $(basename "${CHECKSUM_FILE}"))"
fi

# --- Dry run exits here ---
if [[ "${DRY_RUN}" == true ]]; then
    log_info "[DRY RUN] Backup file validated. No restore performed."
    exit 0
fi

# --- Test database connectivity ---
log_info "--- Step 2: Test database connectivity ---"
if ! psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1;" > /dev/null 2>&1; then
    log_fail "Cannot connect to database ${DB_NAME}@${DB_HOST}:${DB_PORT}"
    exit 1
fi
log_pass "Database connection OK"

# ===========================================================================
# CREATE TEMP DATABASE
# ===========================================================================

log_info "--- Step 3: Create temp database ---"

psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres -c \
    "CREATE DATABASE ${TEST_DB_NAME};" > /dev/null 2>&1

if [[ $? -ne 0 ]]; then
    log_fail "Could not create temp database ${TEST_DB_NAME}"
    exit 1
fi
CLEANUP_NEEDED=true
log_pass "Temp database created: ${TEST_DB_NAME}"

# Enable pgvector extension in the temp DB (required for vector columns)
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -c \
    "CREATE EXTENSION IF NOT EXISTS vector;" > /dev/null 2>&1 \
    || log_warn "Could not create pgvector extension (may not be installed)"

# ===========================================================================
# RESTORE TO TEMP DATABASE
# ===========================================================================

log_info "--- Step 4: Restore backup to temp database ---"
RESTORE_START=$(date +%s)

PSQL_QUIET="--quiet"
if [[ "${VERBOSE}" == true ]]; then
    PSQL_QUIET=""
fi

case "${BACKUP_FILE}" in
    *.dump)
        pg_restore \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${TEST_DB_NAME}" \
            --no-owner \
            --no-privileges \
            --single-transaction \
            --exit-on-error \
            "${BACKUP_FILE}" \
            2>&1 | if [[ "${VERBOSE}" == true ]]; then cat; else tail -5; fi
        ;;
    *.sql.gz)
        gunzip -c "${BACKUP_FILE}" | psql \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${TEST_DB_NAME}" \
            ${PSQL_QUIET} \
            --set ON_ERROR_STOP=1 \
            --single-transaction
        ;;
    *.sql)
        psql \
            -h "${DB_HOST}" \
            -p "${DB_PORT}" \
            -U "${DB_USER}" \
            -d "${TEST_DB_NAME}" \
            ${PSQL_QUIET} \
            --set ON_ERROR_STOP=1 \
            --single-transaction \
            -f "${BACKUP_FILE}"
        ;;
esac

RESTORE_END=$(date +%s)
RESTORE_DURATION=$((RESTORE_END - RESTORE_START))
log_pass "Restore completed in ${RESTORE_DURATION}s"

# ===========================================================================
# VALIDATION
# ===========================================================================

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

record_pass() { PASS_COUNT=$((PASS_COUNT + 1)); log_pass "$@"; }
record_fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); log_fail "$@"; }
record_warn() { WARN_COUNT=$((WARN_COUNT + 1)); log_warn "$@"; }

# --- Check 1: Table count ---
log_info "--- Step 5: Validate restored data ---"

TABLE_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -tAc "
    SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';
" 2>/dev/null || echo "0")

if [[ "${TABLE_COUNT}" -ge "${MIN_TABLES}" ]]; then
    record_pass "Table count: ${TABLE_COUNT} (minimum: ${MIN_TABLES})"
else
    record_fail "Table count: ${TABLE_COUNT} (expected >= ${MIN_TABLES})"
fi

# --- Check 2: Critical table row counts ---
log_info "  Critical table row counts:"
CRITICAL_TABLES="users jobs bids projects hitl_queue leads"
for table in ${CRITICAL_TABLES}; do
    row_count=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -tAc "
        SELECT count(*) FROM ${table};
    " 2>/dev/null || echo "N/A")

    if [[ "${row_count}" == "N/A" ]]; then
        record_warn "Table '${table}' not found in backup"
    else
        log_info "    ${table}: ${row_count} rows"
    fi
done

# --- Check 3: Total row count across all tables ---
TOTAL_ROWS=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -tAc "
    SELECT coalesce(sum(n_live_tup), 0) FROM pg_stat_user_tables;
" 2>/dev/null || echo "0")

if [[ "${TOTAL_ROWS}" -gt 0 ]]; then
    record_pass "Total rows: ${TOTAL_ROWS}"
else
    record_warn "Total rows: ${TOTAL_ROWS} (backup may be from empty database)"
fi

# --- Check 4: Alembic version ---
ALEMBIC_VER=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -tAc "
    SELECT version_num FROM alembic_version LIMIT 1;
" 2>/dev/null || echo "")

if [[ -n "${ALEMBIC_VER}" ]]; then
    record_pass "Alembic version: ${ALEMBIC_VER}"
else
    record_warn "No alembic_version table found (may be pre-migration backup)"
fi

# --- Check 5: Compare with source DB alembic version ---
SOURCE_ALEMBIC=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
    SELECT version_num FROM alembic_version LIMIT 1;
" 2>/dev/null || echo "")

if [[ -n "${ALEMBIC_VER}" && -n "${SOURCE_ALEMBIC}" ]]; then
    if [[ "${ALEMBIC_VER}" == "${SOURCE_ALEMBIC}" ]]; then
        record_pass "Alembic version matches source: ${ALEMBIC_VER}"
    else
        record_warn "Alembic version mismatch: backup=${ALEMBIC_VER}, source=${SOURCE_ALEMBIC}"
    fi
fi

# --- Check 6: pgvector extension ---
PGVECTOR_VER=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -tAc "
    SELECT extversion FROM pg_extension WHERE extname = 'vector';
" 2>/dev/null || echo "")

if [[ -n "${PGVECTOR_VER}" ]]; then
    record_pass "pgvector extension: v${PGVECTOR_VER}"
else
    record_warn "pgvector extension not found in restored database"
fi

# --- Check 7: Foreign key constraints ---
FK_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -tAc "
    SELECT count(*) FROM information_schema.table_constraints
    WHERE constraint_type = 'FOREIGN KEY' AND table_schema = 'public';
" 2>/dev/null || echo "0")

if [[ "${FK_COUNT}" -gt 0 ]]; then
    record_pass "Foreign key constraints: ${FK_COUNT}"
else
    record_warn "No foreign key constraints found"
fi

# --- Check 8: Index count ---
INDEX_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -tAc "
    SELECT count(*) FROM pg_indexes WHERE schemaname = 'public';
" 2>/dev/null || echo "0")
log_info "  Indexes: ${INDEX_COUNT}"

# --- Check 9: Detailed table listing ---
log_info "  All tables:"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB_NAME}" -c "
    SELECT relname AS table_name, n_live_tup AS row_count
    FROM pg_stat_user_tables
    ORDER BY n_live_tup DESC;
" 2>/dev/null || log_warn "Could not list tables"

# ===========================================================================
# KEEP DB (optional)
# ===========================================================================

if [[ "${KEEP_DB}" == true ]]; then
    log_info "Keeping temp database for inspection: ${TEST_DB_NAME}"
    log_info "Connect with: psql ${TEST_DB_URL}"
    CLEANUP_NEEDED=false
fi

# ===========================================================================
# RESULTS SUMMARY
# ===========================================================================

OVERALL="PASS"
EXIT_RESULT=0
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    OVERALL="FAIL"
    EXIT_RESULT=1
elif [[ ${WARN_COUNT} -gt 0 ]]; then
    OVERALL="PASS (with warnings)"
fi

echo ""
log_info "========================================="
log_info "=== DR Test Results: ${OVERALL}"
log_info "========================================="
log_info "Backup:           $(basename "${BACKUP_FILE}")"
log_info "Backup size:      ${BACKUP_SIZE} bytes"
log_info "Restore time:     ${RESTORE_DURATION}s"
log_info "Tables restored:  ${TABLE_COUNT}"
log_info "Total rows:       ${TOTAL_ROWS}"
log_info "Alembic version:  ${ALEMBIC_VER:-N/A}"
log_info "FK constraints:   ${FK_COUNT}"
log_info "Indexes:          ${INDEX_COUNT}"
log_info "Checks passed:    ${PASS_COUNT}"
log_info "Checks failed:    ${FAIL_COUNT}"
log_info "Warnings:         ${WARN_COUNT}"
log_info "========================================="

# RTO estimation
if [[ ${RESTORE_DURATION} -lt 3600 ]]; then
    RTO_MSG="${RESTORE_DURATION}s (within 1-hour RTO target)"
else
    RTO_MSG="${RESTORE_DURATION}s (EXCEEDS 1-hour RTO target!)"
fi
log_info "RTO estimate:     ${RTO_MSG}"

# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

if [[ "${TELEGRAM_NOTIFY}" == true ]]; then
    EMOJI="[OK]"
    if [[ "${OVERALL}" == "FAIL" ]]; then
        EMOJI="[FAIL]"
    fi

    send_telegram_alert "${EMOJI} <b>MAS DR Test: ${OVERALL}</b>%0A%0ABackup: $(basename "${BACKUP_FILE}")%0ARestore time: ${RESTORE_DURATION}s%0ATables: ${TABLE_COUNT}%0ARows: ${TOTAL_ROWS}%0AAlembic: ${ALEMBIC_VER:-N/A}%0APassed: ${PASS_COUNT}, Failed: ${FAIL_COUNT}, Warnings: ${WARN_COUNT}%0ARTO estimate: ${RTO_MSG}"
fi

exit ${EXIT_RESULT}
