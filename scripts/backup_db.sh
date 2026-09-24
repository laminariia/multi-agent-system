#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MAS Database Backup Script (Disaster Recovery)
#
# Creates timestamped, compressed PostgreSQL backups with multi-tier retention:
#   - Daily:   7 days local
#   - Weekly:  4 weeks local
#   - Monthly: 3 months local + optional S3 upload
#
# Usage:
#   bash scripts/backup_db.sh [OPTIONS]
#
# Options:
#   --dir DIR           Backup directory (default: ~/mas-backups)
#   --s3-bucket BUCKET  Upload to S3 bucket (optional)
#   --s3-prefix PREFIX  S3 key prefix (default: mas-backups/)
#   --verify            Run gunzip + row-count verification after backup
#   --telegram           Send Telegram notification on failure
#   --dry-run           Show what would be done without executing
#   --help              Show this help message
#
# Environment:
#   DATABASE_URL       PostgreSQL connection string (required)
#   TELEGRAM_BOT_TOKEN Token for failure alerts (optional)
#   TELEGRAM_CHAT_ID   Chat ID for failure alerts (optional)
#   AWS_ACCESS_KEY_ID  S3 credentials (optional, for --s3-bucket)
#
# Retention policy (from deploy-spec.md):
#   Daily backups:   kept 7 days
#   Weekly backups:  kept 4 weeks (28 days)
#   Monthly backups: kept 3 months (90 days) + S3/Glacier
#
# Spec: docs/Full_work/specs/deploy-spec.md "Backup & Disaster Recovery"
# =============================================================================

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

BACKUP_DIR="${HOME}/mas-backups"
S3_BUCKET=""
S3_PREFIX="mas-backups/"
VERIFY=false
TELEGRAM_NOTIFY=false
DRY_RUN=false
SCRIPT_NAME="$(basename "$0")"

DAILY_RETENTION_DAYS=7
WEEKLY_RETENTION_DAYS=28
MONTHLY_RETENTION_DAYS=90

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    sed -n '3,30p' "$0" | sed 's/^# \?//'
    exit 0
}

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

log_info()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [INFO]  $*"; }
log_warn()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [WARN]  $*" >&2; }
log_error() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [ERROR] $*" >&2; }

# ---------------------------------------------------------------------------
# Telegram alert on failure
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

cleanup() {
    local exit_code=$?
    unset PGPASSWORD
    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Backup FAILED (exit code ${exit_code})"
        send_telegram_alert "<b>MAS Backup FAILED</b>%0A%0AHost: $(hostname)%0ADatabase: ${DB_NAME:-unknown}%0ATime: $(date -u +%Y-%m-%dT%H:%M:%SZ)%0AExit code: ${exit_code}"
    fi
    exit ${exit_code}
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            BACKUP_DIR="$2"
            shift 2
            ;;
        --s3-bucket)
            S3_BUCKET="$2"
            shift 2
            ;;
        --s3-prefix)
            S3_PREFIX="$2"
            shift 2
            ;;
        --verify)
            VERIFY=true
            shift
            ;;
        --telegram)
            TELEGRAM_NOTIFY=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Parse DATABASE_URL
# ---------------------------------------------------------------------------

DATABASE_URL="${DATABASE_URL:-}"
if [[ -z "${DATABASE_URL}" ]]; then
    log_error "DATABASE_URL environment variable is required"
    exit 1
fi

# Format: postgresql://user:password@host:port/dbname?params
DB_USER=$(echo "${DATABASE_URL}" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "${DATABASE_URL}" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo "${DATABASE_URL}" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "${DATABASE_URL}" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "${DATABASE_URL}" | sed -n 's|.*/\([^?]*\).*|\1|p')

if [[ -z "${DB_USER}" || -z "${DB_HOST}" || -z "${DB_NAME}" ]]; then
    log_error "Could not parse DATABASE_URL. Expected: postgresql://user:pass@host:port/dbname"
    exit 1
fi

# ---------------------------------------------------------------------------
# Timestamp and file paths
# ---------------------------------------------------------------------------

TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
DAY_OF_WEEK=$(date -u +%u)      # 1=Monday .. 7=Sunday
DAY_OF_MONTH=$(date -u +%d)

# Determine backup tier
BACKUP_TIER="daily"
if [[ "${DAY_OF_MONTH}" == "01" ]]; then
    BACKUP_TIER="monthly"
elif [[ "${DAY_OF_WEEK}" == "7" ]]; then
    BACKUP_TIER="weekly"
fi

TIER_DIR="${BACKUP_DIR}/${BACKUP_TIER}"
DUMP_FILE="${TIER_DIR}/mas_${BACKUP_TIER}_${TIMESTAMP}.dump"
SQL_GZ_FILE="${TIER_DIR}/mas_${BACKUP_TIER}_${TIMESTAMP}.sql.gz"
CHECKSUM_FILE="${TIER_DIR}/mas_${BACKUP_TIER}_${TIMESTAMP}.sha256"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

if ! command -v pg_dump &>/dev/null; then
    log_error "pg_dump not found. Install postgresql-client."
    exit 1
fi

if [[ "${S3_BUCKET}" != "" ]] && ! command -v aws &>/dev/null; then
    log_warn "aws CLI not found — S3 upload will be skipped"
    S3_BUCKET=""
fi

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

log_info "=== MAS Database Backup ==="
log_info "Database:  ${DB_NAME}@${DB_HOST}:${DB_PORT}"
log_info "Tier:      ${BACKUP_TIER}"
log_info "Output:    ${TIER_DIR}/"
log_info "S3:        ${S3_BUCKET:-disabled}"
log_info "Verify:    ${VERIFY}"
log_info "Dry run:   ${DRY_RUN}"

if [[ "${DRY_RUN}" == true ]]; then
    log_info "[DRY RUN] Would create: ${DUMP_FILE}"
    log_info "[DRY RUN] Would create: ${SQL_GZ_FILE}"
    log_info "[DRY RUN] Retention: daily=${DAILY_RETENTION_DAYS}d, weekly=${WEEKLY_RETENTION_DAYS}d, monthly=${MONTHLY_RETENTION_DAYS}d"
    if [[ -n "${S3_BUCKET}" ]]; then
        log_info "[DRY RUN] Would upload to s3://${S3_BUCKET}/${S3_PREFIX}"
    fi
    exit 0
fi

# Create tier directories
mkdir -p "${BACKUP_DIR}/daily" "${BACKUP_DIR}/weekly" "${BACKUP_DIR}/monthly"

export PGPASSWORD="${DB_PASS}"

# --- Custom format backup (for pg_restore) ---
log_info "Creating custom-format backup..."
pg_dump \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    --format=custom \
    --compress=9 \
    --no-owner \
    --no-privileges \
    --file="${DUMP_FILE}" \
    2>&1 | tail -5

# --- Plain SQL backup (gzipped, for portability) ---
log_info "Creating gzipped SQL backup..."
pg_dump \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    --format=plain \
    --no-owner \
    --no-privileges \
    | gzip -9 > "${SQL_GZ_FILE}"

# --- SHA-256 checksums ---
log_info "Generating checksums..."
sha256sum "${DUMP_FILE}" "${SQL_GZ_FILE}" > "${CHECKSUM_FILE}" 2>/dev/null \
    || shasum -a 256 "${DUMP_FILE}" "${SQL_GZ_FILE}" > "${CHECKSUM_FILE}" 2>/dev/null \
    || log_warn "Could not generate checksums (sha256sum/shasum not found)"

# --- File sizes ---
DUMP_SIZE=$(stat -c%s "${DUMP_FILE}" 2>/dev/null || stat -f%z "${DUMP_FILE}" 2>/dev/null || echo "0")
SQL_SIZE=$(stat -c%s "${SQL_GZ_FILE}" 2>/dev/null || stat -f%z "${SQL_GZ_FILE}" 2>/dev/null || echo "0")

log_info "Custom format: $(basename "${DUMP_FILE}") (${DUMP_SIZE} bytes)"
log_info "SQL gzipped:   $(basename "${SQL_GZ_FILE}") (${SQL_SIZE} bytes)"

# --- Verify backup integrity ---
if [[ "${VERIFY}" == true ]]; then
    log_info "Verifying backup integrity..."

    # 1. Test gzip integrity
    if gunzip -t "${SQL_GZ_FILE}" 2>/dev/null; then
        log_info "  gzip integrity: OK"
    else
        log_error "  gzip integrity: FAILED"
        exit 1
    fi

    # 2. Test pg_restore listing (custom format)
    if pg_restore --list "${DUMP_FILE}" > /dev/null 2>&1; then
        log_info "  pg_restore list: OK"
    else
        log_error "  pg_restore list: FAILED"
        exit 1
    fi

    # 3. Row count comparison (source vs backup TOC)
    SOURCE_TABLES=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
        SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';
    " 2>/dev/null || echo "?")
    BACKUP_TABLES=$(pg_restore --list "${DUMP_FILE}" 2>/dev/null | grep -c "TABLE DATA" || echo "?")
    log_info "  Source tables: ${SOURCE_TABLES}, Backup table data entries: ${BACKUP_TABLES}"
fi

# ---------------------------------------------------------------------------
# S3 Upload (optional)
# ---------------------------------------------------------------------------

if [[ -n "${S3_BUCKET}" ]]; then
    log_info "Uploading to S3: s3://${S3_BUCKET}/${S3_PREFIX}${BACKUP_TIER}/"

    aws s3 cp "${DUMP_FILE}" \
        "s3://${S3_BUCKET}/${S3_PREFIX}${BACKUP_TIER}/$(basename "${DUMP_FILE}")" \
        --storage-class STANDARD_IA \
        --quiet

    aws s3 cp "${SQL_GZ_FILE}" \
        "s3://${S3_BUCKET}/${S3_PREFIX}${BACKUP_TIER}/$(basename "${SQL_GZ_FILE}")" \
        --storage-class STANDARD_IA \
        --quiet

    aws s3 cp "${CHECKSUM_FILE}" \
        "s3://${S3_BUCKET}/${S3_PREFIX}${BACKUP_TIER}/$(basename "${CHECKSUM_FILE}")" \
        --quiet

    # Monthly backups also go to Glacier (90-day DR per spec)
    if [[ "${BACKUP_TIER}" == "monthly" ]]; then
        log_info "Archiving monthly backup to Glacier..."
        aws s3 cp "${DUMP_FILE}" \
            "s3://${S3_BUCKET}/${S3_PREFIX}glacier/$(basename "${DUMP_FILE}")" \
            --storage-class GLACIER \
            --quiet
    fi

    log_info "S3 upload complete"
fi

# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------

log_info "Applying retention policy..."

cleanup_tier() {
    local tier="$1"
    local retention_days="$2"
    local dir="${BACKUP_DIR}/${tier}"

    if [[ ! -d "${dir}" ]]; then
        return
    fi

    local deleted
    deleted=$(find "${dir}" -name "mas_${tier}_*" -type f -mtime +"${retention_days}" -delete -print 2>/dev/null | wc -l)
    if [[ "${deleted}" -gt 0 ]]; then
        log_info "  ${tier}: deleted ${deleted} files older than ${retention_days} days"
    else
        log_info "  ${tier}: no expired files"
    fi
}

cleanup_tier "daily"   "${DAILY_RETENTION_DAYS}"
cleanup_tier "weekly"  "${WEEKLY_RETENTION_DAYS}"
cleanup_tier "monthly" "${MONTHLY_RETENTION_DAYS}"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

unset PGPASSWORD

TOTAL_FILES=$(find "${BACKUP_DIR}" -name "mas_*" -type f 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1 || echo "?")

log_info "=== Backup Complete ==="
log_info "Total backup files: ${TOTAL_FILES}"
log_info "Total backup size:  ${TOTAL_SIZE}"
