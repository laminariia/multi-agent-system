#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MAS Health Check Script (Disaster Recovery)
#
# Checks the health of all MAS infrastructure components:
#   - PostgreSQL: connectivity, replication lag, table counts
#   - Valkey: connectivity, memory usage, key count
#   - API: /health endpoint, response time
#   - Dashboard: HTTP status, response time
#
# Outputs JSON report to stdout. Human-readable format with --pretty.
#
# Usage:
#   bash scripts/health_check.sh [OPTIONS]
#
# Options:
#   --api-url URL        API base URL (default: http://localhost:8000)
#   --dashboard-url URL  Dashboard URL (default: http://localhost:3000)
#   --pretty             Human-readable output instead of JSON
#   --timeout SECS       Per-check timeout in seconds (default: 10)
#   --dry-run            Show what would be checked without connecting
#   --help               Show this help message
#
# Environment:
#   DATABASE_URL         PostgreSQL connection string
#   VALKEY_URL           Valkey/Redis connection string
#
# Exit codes:
#   0 = all healthy
#   1 = one or more checks degraded
#   2 = critical failure (database or API down)
#
# Spec: docs/Full_work/specs/deploy-spec.md "Health Checks"
# =============================================================================

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

API_URL="${API_URL:-http://localhost:8000}"
DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:3000}"
PRETTY=false
TIMEOUT=10
DRY_RUN=false
EXIT_CODE=0

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    sed -n '3,32p' "$0" | sed 's/^# \?//'
    exit 0
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --dashboard-url)
            DASHBOARD_URL="$2"
            shift 2
            ;;
        --pretty)
            PRETTY=true
            shift
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# JSON-safe string escaping
json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/}"
    echo -n "${s}"
}

# Measure command execution time in milliseconds
measure_ms() {
    local start end
    start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))" 2>/dev/null || echo "0")
    eval "$@" > /dev/null 2>&1
    local rc=$?
    end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))" 2>/dev/null || echo "0")
    # Fallback if nanosecond precision unavailable
    if [[ "${start}" == "0" || "${end}" == "0" ]]; then
        echo "0"
    else
        echo $(( (end - start) / 1000000 ))
    fi
    return ${rc}
}

# ---------------------------------------------------------------------------
# Parse DATABASE_URL
# ---------------------------------------------------------------------------

DATABASE_URL="${DATABASE_URL:-}"
DB_USER="" DB_PASS="" DB_HOST="" DB_PORT="" DB_NAME=""

if [[ -n "${DATABASE_URL}" ]]; then
    DB_USER=$(echo "${DATABASE_URL}" | sed -n 's|.*://\([^:]*\):.*|\1|p')
    DB_PASS=$(echo "${DATABASE_URL}" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
    DB_HOST=$(echo "${DATABASE_URL}" | sed -n 's|.*@\([^:]*\):.*|\1|p')
    DB_PORT=$(echo "${DATABASE_URL}" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
    DB_NAME=$(echo "${DATABASE_URL}" | sed -n 's|.*/\([^?]*\).*|\1|p')
fi

# ---------------------------------------------------------------------------
# Parse VALKEY_URL
# ---------------------------------------------------------------------------

VALKEY_URL="${VALKEY_URL:-}"
VK_HOST="" VK_PORT=""

if [[ -n "${VALKEY_URL}" ]]; then
    # Format: redis://[:password@]host:port[/db] or valkey://...
    VK_HOST=$(echo "${VALKEY_URL}" | sed -n 's|.*@\([^:]*\):.*|\1|p')
    if [[ -z "${VK_HOST}" ]]; then
        VK_HOST=$(echo "${VALKEY_URL}" | sed -n 's|.*://\([^:/@]*\).*|\1|p')
    fi
    VK_PORT=$(echo "${VALKEY_URL}" | sed -n 's|.*:\([0-9]*\).*|\1|p' | tail -1)
    VK_PORT="${VK_PORT:-6379}"
fi

# ===========================================================================
# DRY RUN
# ===========================================================================

if [[ "${DRY_RUN}" == true ]]; then
    echo "{"
    echo "  \"mode\": \"dry_run\","
    echo "  \"checks\": ["
    echo "    {\"name\": \"postgresql\", \"target\": \"${DB_HOST:-unconfigured}:${DB_PORT:-5432}/${DB_NAME:-unconfigured}\"},"
    echo "    {\"name\": \"valkey\", \"target\": \"${VK_HOST:-unconfigured}:${VK_PORT:-6379}\"},"
    echo "    {\"name\": \"api\", \"target\": \"${API_URL}/health\"},"
    echo "    {\"name\": \"dashboard\", \"target\": \"${DASHBOARD_URL}\"}"
    echo "  ]"
    echo "}"
    exit 0
fi

# ===========================================================================
# CHECK: PostgreSQL
# ===========================================================================

PG_STATUS="unknown"
PG_LATENCY_MS=0
PG_VERSION=""
PG_TABLE_COUNT=""
PG_DB_SIZE=""
PG_ACTIVE_CONNS=""
PG_ALEMBIC=""
PG_ERROR=""

check_postgresql() {
    if [[ -z "${DATABASE_URL}" ]]; then
        PG_STATUS="unconfigured"
        PG_ERROR="DATABASE_URL not set"
        return
    fi

    export PGPASSWORD="${DB_PASS}"
    local start end

    start=$(date +%s%N 2>/dev/null || echo "0")
    if ! psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
        -c "SELECT 1;" > /dev/null 2>&1; then
        PG_STATUS="down"
        PG_ERROR="Connection failed"
        EXIT_CODE=2
        unset PGPASSWORD
        return
    fi
    end=$(date +%s%N 2>/dev/null || echo "0")
    if [[ "${start}" != "0" && "${end}" != "0" ]]; then
        PG_LATENCY_MS=$(( (end - start) / 1000000 ))
    fi

    PG_STATUS="healthy"

    PG_VERSION=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
        SELECT version();
    " 2>/dev/null | head -1 || echo "")

    PG_TABLE_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
        SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';
    " 2>/dev/null || echo "0")

    PG_DB_SIZE=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
        SELECT pg_size_pretty(pg_database_size(current_database()));
    " 2>/dev/null || echo "?")

    PG_ACTIVE_CONNS=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
        SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();
    " 2>/dev/null || echo "0")

    PG_ALEMBIC=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -tAc "
        SELECT version_num FROM alembic_version LIMIT 1;
    " 2>/dev/null || echo "?")

    unset PGPASSWORD
}

# ===========================================================================
# CHECK: Valkey
# ===========================================================================

VK_STATUS="unknown"
VK_LATENCY_MS=0
VK_VERSION=""
VK_USED_MEMORY=""
VK_MAX_MEMORY=""
VK_KEY_COUNT=""
VK_ERROR=""

check_valkey() {
    if [[ -z "${VALKEY_URL}" ]]; then
        VK_STATUS="unconfigured"
        VK_ERROR="VALKEY_URL not set"
        return
    fi

    if ! command -v redis-cli &>/dev/null; then
        # Try connecting via Python as fallback
        local py_cmd
        py_cmd=$(command -v python3 || command -v python || echo "")
        if [[ -n "${py_cmd}" ]]; then
            local py_result
            py_result=$("${py_cmd}" -c "
import redis, json, time
try:
    start = time.monotonic()
    r = redis.from_url('${VALKEY_URL}')
    r.ping()
    latency = int((time.monotonic() - start) * 1000)
    info = r.info()
    print(json.dumps({
        'status': 'healthy',
        'latency_ms': latency,
        'version': info.get('redis_version', '?'),
        'used_memory_human': info.get('used_memory_human', '?'),
        'maxmemory_human': info.get('maxmemory_human', '?'),
        'db0_keys': info.get('db0', {}).get('keys', 0) if isinstance(info.get('db0'), dict) else 0,
    }))
except Exception as e:
    print(json.dumps({'status': 'down', 'error': str(e)}))
" 2>/dev/null || echo '{"status":"error","error":"python check failed"}')

            VK_STATUS=$(echo "${py_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")
            VK_LATENCY_MS=$(echo "${py_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('latency_ms',0))" 2>/dev/null || echo "0")
            VK_VERSION=$(echo "${py_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || echo "")
            VK_USED_MEMORY=$(echo "${py_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('used_memory_human',''))" 2>/dev/null || echo "")
            VK_MAX_MEMORY=$(echo "${py_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('maxmemory_human',''))" 2>/dev/null || echo "")
            VK_KEY_COUNT=$(echo "${py_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('db0_keys',0))" 2>/dev/null || echo "0")
            VK_ERROR=$(echo "${py_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null || echo "")

            if [[ "${VK_STATUS}" == "down" ]]; then
                EXIT_CODE=1
            fi
            return
        fi

        VK_STATUS="unchecked"
        VK_ERROR="redis-cli and python not available"
        return
    fi

    # Use redis-cli
    local start end
    start=$(date +%s%N 2>/dev/null || echo "0")
    if ! timeout "${TIMEOUT}" redis-cli -u "${VALKEY_URL}" PING > /dev/null 2>&1; then
        VK_STATUS="down"
        VK_ERROR="PING failed"
        EXIT_CODE=1
        return
    fi
    end=$(date +%s%N 2>/dev/null || echo "0")
    if [[ "${start}" != "0" && "${end}" != "0" ]]; then
        VK_LATENCY_MS=$(( (end - start) / 1000000 ))
    fi

    VK_STATUS="healthy"
    VK_VERSION=$(redis-cli -u "${VALKEY_URL}" INFO server 2>/dev/null | grep "redis_version:" | cut -d: -f2 | tr -d '\r' || echo "?")
    VK_USED_MEMORY=$(redis-cli -u "${VALKEY_URL}" INFO memory 2>/dev/null | grep "used_memory_human:" | cut -d: -f2 | tr -d '\r' || echo "?")
    VK_MAX_MEMORY=$(redis-cli -u "${VALKEY_URL}" INFO memory 2>/dev/null | grep "maxmemory_human:" | cut -d: -f2 | tr -d '\r' || echo "?")
    VK_KEY_COUNT=$(redis-cli -u "${VALKEY_URL}" DBSIZE 2>/dev/null | grep -oE '[0-9]+' || echo "0")
}

# ===========================================================================
# CHECK: API
# ===========================================================================

API_STATUS="unknown"
API_LATENCY_MS=0
API_HEALTH_BODY=""
API_ERROR=""

check_api() {
    local start end http_code body

    start=$(date +%s%N 2>/dev/null || echo "0")
    http_code=$(curl -sf -o /tmp/mas_health_api.json -w "%{http_code}" \
        --connect-timeout "${TIMEOUT}" \
        --max-time "${TIMEOUT}" \
        "${API_URL}/health" 2>/dev/null || echo "000")
    end=$(date +%s%N 2>/dev/null || echo "0")

    if [[ "${start}" != "0" && "${end}" != "0" ]]; then
        API_LATENCY_MS=$(( (end - start) / 1000000 ))
    fi

    if [[ "${http_code}" == "200" ]]; then
        API_STATUS="healthy"
        API_HEALTH_BODY=$(cat /tmp/mas_health_api.json 2>/dev/null || echo "{}")
    elif [[ "${http_code}" == "000" ]]; then
        API_STATUS="down"
        API_ERROR="Connection refused or timeout"
        EXIT_CODE=2
    else
        API_STATUS="degraded"
        API_ERROR="HTTP ${http_code}"
        EXIT_CODE=1
    fi

    rm -f /tmp/mas_health_api.json
}

# ===========================================================================
# CHECK: Dashboard
# ===========================================================================

DASH_STATUS="unknown"
DASH_LATENCY_MS=0
DASH_ERROR=""

check_dashboard() {
    local start end http_code

    start=$(date +%s%N 2>/dev/null || echo "0")
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
        --connect-timeout "${TIMEOUT}" \
        --max-time "${TIMEOUT}" \
        "${DASHBOARD_URL}" 2>/dev/null || echo "000")
    end=$(date +%s%N 2>/dev/null || echo "0")

    if [[ "${start}" != "0" && "${end}" != "0" ]]; then
        DASH_LATENCY_MS=$(( (end - start) / 1000000 ))
    fi

    if [[ "${http_code}" == "200" ]]; then
        DASH_STATUS="healthy"
    elif [[ "${http_code}" == "000" ]]; then
        DASH_STATUS="down"
        DASH_ERROR="Connection refused or timeout"
        # Dashboard down is degraded, not critical
        if [[ ${EXIT_CODE} -lt 1 ]]; then
            EXIT_CODE=1
        fi
    else
        DASH_STATUS="degraded"
        DASH_ERROR="HTTP ${http_code}"
        if [[ ${EXIT_CODE} -lt 1 ]]; then
            EXIT_CODE=1
        fi
    fi
}

# ===========================================================================
# Run all checks
# ===========================================================================

check_postgresql
check_valkey
check_api
check_dashboard

# ===========================================================================
# Determine overall status
# ===========================================================================

OVERALL="healthy"
if [[ ${EXIT_CODE} -eq 2 ]]; then
    OVERALL="unhealthy"
elif [[ ${EXIT_CODE} -eq 1 ]]; then
    OVERALL="degraded"
fi

# ===========================================================================
# Output: JSON
# ===========================================================================

if [[ "${PRETTY}" == true ]]; then
    echo "=== MAS Health Check ==="
    echo "Timestamp: ${TIMESTAMP}"
    echo "Overall:   ${OVERALL}"
    echo ""
    echo "--- PostgreSQL ---"
    echo "  Status:       ${PG_STATUS}"
    echo "  Latency:      ${PG_LATENCY_MS}ms"
    echo "  Version:      ${PG_VERSION:-N/A}"
    echo "  Tables:       ${PG_TABLE_COUNT:-N/A}"
    echo "  DB Size:      ${PG_DB_SIZE:-N/A}"
    echo "  Connections:  ${PG_ACTIVE_CONNS:-N/A}"
    echo "  Alembic:      ${PG_ALEMBIC:-N/A}"
    if [[ -n "${PG_ERROR}" ]]; then
        echo "  Error:        ${PG_ERROR}"
    fi
    echo ""
    echo "--- Valkey ---"
    echo "  Status:       ${VK_STATUS}"
    echo "  Latency:      ${VK_LATENCY_MS}ms"
    echo "  Version:      ${VK_VERSION:-N/A}"
    echo "  Memory:       ${VK_USED_MEMORY:-N/A} / ${VK_MAX_MEMORY:-N/A}"
    echo "  Keys:         ${VK_KEY_COUNT:-N/A}"
    if [[ -n "${VK_ERROR}" ]]; then
        echo "  Error:        ${VK_ERROR}"
    fi
    echo ""
    echo "--- API ---"
    echo "  Status:       ${API_STATUS}"
    echo "  Latency:      ${API_LATENCY_MS}ms"
    echo "  URL:          ${API_URL}/health"
    if [[ -n "${API_ERROR}" ]]; then
        echo "  Error:        ${API_ERROR}"
    fi
    echo ""
    echo "--- Dashboard ---"
    echo "  Status:       ${DASH_STATUS}"
    echo "  Latency:      ${DASH_LATENCY_MS}ms"
    echo "  URL:          ${DASHBOARD_URL}"
    if [[ -n "${DASH_ERROR}" ]]; then
        echo "  Error:        ${DASH_ERROR}"
    fi
    echo ""
    echo "========================="
else
    cat <<ENDJSON
{
  "timestamp": "${TIMESTAMP}",
  "overall_status": "${OVERALL}",
  "checks": {
    "postgresql": {
      "status": "${PG_STATUS}",
      "latency_ms": ${PG_LATENCY_MS},
      "version": "$(json_escape "${PG_VERSION}")",
      "table_count": "${PG_TABLE_COUNT}",
      "db_size": "$(json_escape "${PG_DB_SIZE}")",
      "active_connections": "${PG_ACTIVE_CONNS}",
      "alembic_version": "${PG_ALEMBIC}",
      "error": "$(json_escape "${PG_ERROR}")"
    },
    "valkey": {
      "status": "${VK_STATUS}",
      "latency_ms": ${VK_LATENCY_MS},
      "version": "$(json_escape "${VK_VERSION}")",
      "used_memory": "$(json_escape "${VK_USED_MEMORY}")",
      "max_memory": "$(json_escape "${VK_MAX_MEMORY}")",
      "key_count": "${VK_KEY_COUNT}",
      "error": "$(json_escape "${VK_ERROR}")"
    },
    "api": {
      "status": "${API_STATUS}",
      "latency_ms": ${API_LATENCY_MS},
      "url": "${API_URL}/health",
      "error": "$(json_escape "${API_ERROR}")"
    },
    "dashboard": {
      "status": "${DASH_STATUS}",
      "latency_ms": ${DASH_LATENCY_MS},
      "url": "${DASHBOARD_URL}",
      "error": "$(json_escape "${DASH_ERROR}")"
    }
  }
}
ENDJSON
fi

exit ${EXIT_CODE}
