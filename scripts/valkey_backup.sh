#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MAS Valkey (Redis-compatible) Backup Script
#
# Creates timestamped Valkey RDB snapshot backups:
#   - Triggers BGSAVE and waits for completion
#   - Copies the RDB file with timestamp
#   - Verifies file integrity (non-zero size)
#   - Rotates: keeps 7 daily snapshots
#   - Optional S3 upload
#
# Usage:
#   bash scripts/valkey_backup.sh [OPTIONS]
#
# Options:
#   --dir DIR            Backup directory (default: ~/mas-backups/valkey)
#   --s3-bucket BUCKET   Upload to S3 bucket (optional)
#   --s3-prefix PREFIX   S3 key prefix (default: mas-backups/valkey/)
#   --retention DAYS     Days to keep local snapshots (default: 7)
#   --timeout SECS       Max seconds to wait for BGSAVE (default: 60)
#   --telegram           Send Telegram notification on failure
#   --dry-run            Show what would be done without executing
#   --help               Show this help message
#
# Environment:
#   VALKEY_URL           Valkey connection URL (required)
#                        Format: valkey://[:password@]host:port[/db]
#                        or:     redis://[:password@]host:port[/db]
#   TELEGRAM_BOT_TOKEN   Token for failure alerts (optional)
#   TELEGRAM_CHAT_ID     Chat ID for failure alerts (optional)
#
# Recovery testing schedule (from deploy-spec.md):
#   Valkey restore test: quarterly
#
# Spec: docs/Full_work/specs/deploy-spec.md "Backup & Disaster Recovery"
# =============================================================================

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

BACKUP_DIR="${HOME}/mas-backups/valkey"
S3_BUCKET=""
S3_PREFIX="mas-backups/valkey/"
RETENTION_DAYS=7
BGSAVE_TIMEOUT=60
TELEGRAM_NOTIFY=false
DRY_RUN=false
SCRIPT_NAME="$(basename "$0")"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    sed -n '3,35p' "$0" | sed 's/^# \?//'
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
    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Valkey backup FAILED (exit code ${exit_code})"
        send_telegram_alert "<b>MAS Valkey Backup FAILED</b>%0A%0AHost: $(hostname)%0ATime: $(date -u +%Y-%m-%dT%H:%M:%SZ)%0AExit code: ${exit_code}"
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
        --retention)
            RETENTION_DAYS="$2"
            shift 2
            ;;
        --timeout)
            BGSAVE_TIMEOUT="$2"
            shift 2
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
# Parse VALKEY_URL
# ---------------------------------------------------------------------------

VALKEY_URL="${VALKEY_URL:-}"
if [[ -z "${VALKEY_URL}" ]]; then
    log_error "VALKEY_URL environment variable is required"
    exit 1
fi

# Determine the CLI command to use (valkey-cli preferred, redis-cli fallback)
CLI_CMD=""
if command -v valkey-cli &>/dev/null; then
    CLI_CMD="valkey-cli"
elif command -v redis-cli &>/dev/null; then
    CLI_CMD="redis-cli"
else
    log_error "Neither valkey-cli nor redis-cli found. Install valkey-tools or redis-tools."
    exit 1
fi

# ---------------------------------------------------------------------------
# Timestamp and file paths
# ---------------------------------------------------------------------------

TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
BACKUP_FILE="valkey_${TIMESTAMP}.rdb"

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

log_info "=== MAS Valkey Backup ==="
log_info "CLI:       ${CLI_CMD}"
log_info "Output:    ${BACKUP_DIR}/${BACKUP_FILE}"
log_info "Retention: ${RETENTION_DAYS} days"
log_info "Timeout:   ${BGSAVE_TIMEOUT}s"
log_info "S3:        ${S3_BUCKET:-disabled}"
log_info "Dry run:   ${DRY_RUN}"

if [[ "${DRY_RUN}" == true ]]; then
    log_info "[DRY RUN] Would trigger BGSAVE and copy RDB to ${BACKUP_DIR}/${BACKUP_FILE}"
    log_info "[DRY RUN] Retention: ${RETENTION_DAYS} days"
    if [[ -n "${S3_BUCKET}" ]]; then
        log_info "[DRY RUN] Would upload to s3://${S3_BUCKET}/${S3_PREFIX}${BACKUP_FILE}"
    fi
    exit 0
fi

# Create backup directory
mkdir -p "${BACKUP_DIR}"

# --- Test connectivity ---
log_info "Testing Valkey connectivity..."
if ! ${CLI_CMD} -u "${VALKEY_URL}" PING > /dev/null 2>&1; then
    log_error "Cannot connect to Valkey at ${VALKEY_URL}"
    exit 1
fi
log_info "  Connection: OK"

# --- Record pre-BGSAVE info ---
KEY_COUNT=$(${CLI_CMD} -u "${VALKEY_URL}" DBSIZE 2>/dev/null | grep -oE '[0-9]+' || echo "?")
USED_MEMORY=$(${CLI_CMD} -u "${VALKEY_URL}" INFO memory 2>/dev/null | grep "used_memory_human:" | cut -d: -f2 | tr -d '\r' || echo "?")
log_info "  Keys: ${KEY_COUNT}"
log_info "  Memory: ${USED_MEMORY}"

# --- Get last BGSAVE timestamp before triggering ---
LAST_SAVE_BEFORE=$(${CLI_CMD} -u "${VALKEY_URL}" LASTSAVE 2>/dev/null || echo "0")

# --- Trigger BGSAVE ---
log_info "Triggering BGSAVE..."
BGSAVE_RESULT=$(${CLI_CMD} -u "${VALKEY_URL}" BGSAVE 2>/dev/null || echo "FAILED")
if [[ "${BGSAVE_RESULT}" == *"FAILED"* ]] && [[ "${BGSAVE_RESULT}" != *"already in progress"* ]]; then
    log_error "BGSAVE command failed: ${BGSAVE_RESULT}"
    exit 1
fi

# --- Wait for BGSAVE to complete ---
log_info "Waiting for BGSAVE to complete (timeout: ${BGSAVE_TIMEOUT}s)..."
WAIT_COUNT=0
while [[ ${WAIT_COUNT} -lt ${BGSAVE_TIMEOUT} ]]; do
    LAST_SAVE_AFTER=$(${CLI_CMD} -u "${VALKEY_URL}" LASTSAVE 2>/dev/null || echo "0")
    if [[ "${LAST_SAVE_AFTER}" != "${LAST_SAVE_BEFORE}" ]]; then
        log_info "  BGSAVE completed after ${WAIT_COUNT}s"
        break
    fi
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

if [[ ${WAIT_COUNT} -ge ${BGSAVE_TIMEOUT} ]]; then
    log_error "BGSAVE did not complete within ${BGSAVE_TIMEOUT}s"
    exit 1
fi

# --- Locate and copy the RDB file ---
log_info "Locating RDB file..."

VALKEY_DIR=$(${CLI_CMD} -u "${VALKEY_URL}" CONFIG GET dir 2>/dev/null | tail -1 || echo "")
RDB_FILENAME=$(${CLI_CMD} -u "${VALKEY_URL}" CONFIG GET dbfilename 2>/dev/null | tail -1 || echo "dump.rdb")

if [[ -z "${VALKEY_DIR}" ]]; then
    log_error "Could not determine Valkey data directory via CONFIG GET dir"
    exit 1
fi

RDB_SOURCE="${VALKEY_DIR}/${RDB_FILENAME}"

if [[ ! -f "${RDB_SOURCE}" ]]; then
    # Try Docker volume path fallback
    if [[ -f "/data/${RDB_FILENAME}" ]]; then
        RDB_SOURCE="/data/${RDB_FILENAME}"
        log_warn "Using fallback RDB path: ${RDB_SOURCE}"
    else
        log_error "RDB file not found at ${RDB_SOURCE} or /data/${RDB_FILENAME}"
        log_warn "If Valkey runs in Docker, mount the data volume or use docker cp"
        exit 1
    fi
fi

# Copy with checksum
cp "${RDB_SOURCE}" "${BACKUP_DIR}/${BACKUP_FILE}"

# --- Verify backup ---
BACKUP_SIZE=$(stat -c%s "${BACKUP_DIR}/${BACKUP_FILE}" 2>/dev/null || stat -f%z "${BACKUP_DIR}/${BACKUP_FILE}" 2>/dev/null || echo "0")
if [[ "${BACKUP_SIZE}" -lt 100 ]]; then
    log_error "Backup file too small (${BACKUP_SIZE} bytes) — likely corrupt"
    exit 1
fi

# Generate SHA-256 checksum
CHECKSUM_FILE="${BACKUP_DIR}/valkey_${TIMESTAMP}.sha256"
sha256sum "${BACKUP_DIR}/${BACKUP_FILE}" > "${CHECKSUM_FILE}" 2>/dev/null \
    || shasum -a 256 "${BACKUP_DIR}/${BACKUP_FILE}" > "${CHECKSUM_FILE}" 2>/dev/null \
    || log_warn "Could not generate checksum"

log_info "Backup created: ${BACKUP_FILE} (${BACKUP_SIZE} bytes)"

# ---------------------------------------------------------------------------
# S3 Upload (optional)
# ---------------------------------------------------------------------------

if [[ -n "${S3_BUCKET}" ]]; then
    if ! command -v aws &>/dev/null; then
        log_warn "aws CLI not found — S3 upload skipped"
    else
        log_info "Uploading to S3: s3://${S3_BUCKET}/${S3_PREFIX}${BACKUP_FILE}"
        aws s3 cp "${BACKUP_DIR}/${BACKUP_FILE}" \
            "s3://${S3_BUCKET}/${S3_PREFIX}${BACKUP_FILE}" \
            --storage-class STANDARD_IA \
            --quiet
        aws s3 cp "${CHECKSUM_FILE}" \
            "s3://${S3_BUCKET}/${S3_PREFIX}$(basename "${CHECKSUM_FILE}")" \
            --quiet
        log_info "S3 upload complete"
    fi
fi

# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------

log_info "Applying retention (${RETENTION_DAYS} days)..."
DELETED=$(find "${BACKUP_DIR}" -name "valkey_*.rdb" -type f -mtime +"${RETENTION_DAYS}" -delete -print 2>/dev/null | wc -l)
# Also clean old checksums
find "${BACKUP_DIR}" -name "valkey_*.sha256" -type f -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true

if [[ "${DELETED}" -gt 0 ]]; then
    log_info "  Deleted ${DELETED} expired RDB files"
else
    log_info "  No expired files"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL_FILES=$(find "${BACKUP_DIR}" -name "valkey_*.rdb" -type f 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1 || echo "?")

log_info "=== Valkey Backup Complete ==="
log_info "Snapshot:    ${BACKUP_FILE}"
log_info "Size:        ${BACKUP_SIZE} bytes"
log_info "Keys:        ${KEY_COUNT}"
log_info "Total files: ${TOTAL_FILES}"
log_info "Total size:  ${TOTAL_SIZE}"
