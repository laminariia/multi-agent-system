#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MAS Rollback Script (Disaster Recovery)
#
# Performs coordinated rollback across Railway, Alembic, and Docker:
#   - Railway: redeploy previous deployment via Railway CLI
#   - Alembic: downgrade database schema by N revisions
#   - Docker:  roll back to previous image tag
#
# Supports rollback of API, Dashboard, or both services.
#
# Usage:
#   bash scripts/rollback.sh <target> [OPTIONS]
#
# Targets:
#   railway     Redeploy previous Railway deployment
#   alembic     Downgrade database schema
#   docker      Roll back Docker images
#   full        All three: docker stop -> alembic downgrade -> railway/docker redeploy
#
# Options:
#   --service SVC        Service to rollback: api, dashboard, all (default: all)
#   --revisions N        Alembic downgrade steps (default: 1)
#   --tag TAG            Docker image tag to rollback to
#   --compose-file FILE  Docker compose file (default: docker-compose.prod.yml)
#   --health-check       Verify /health after rollback
#   --dry-run            Show planned actions without executing
#   --help               Show this help message
#
# Environment:
#   DATABASE_URL         For Alembic downgrade
#   RAILWAY_TOKEN        For Railway CLI (optional)
#
# Spec: docs/Full_work/specs/deploy-spec.md "Rollback Procedure"
# =============================================================================

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

TARGET=""
SERVICE="all"
REVISIONS=1
DOCKER_TAG=""
COMPOSE_FILE="docker-compose.prod.yml"
HEALTH_CHECK=false
DRY_RUN=false
PROJECT_DIR="${MAS_PROJECT_DIR:-/opt/mas}"
API_URL="${API_URL:-http://localhost:8000}"
DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:3000}"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    sed -n '3,34p' "$0" | sed 's/^# \?//'
    exit 0
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log_info()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [INFO]  $*"; }
log_warn()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [WARN]  $*" >&2; }
log_error() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [ERROR] $*" >&2; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
    usage
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        railway|alembic|docker|full)
            TARGET="$1"
            shift
            ;;
        --service)
            SERVICE="$2"
            shift 2
            ;;
        --revisions)
            REVISIONS="$2"
            shift 2
            ;;
        --tag)
            DOCKER_TAG="$2"
            shift 2
            ;;
        --compose-file)
            COMPOSE_FILE="$2"
            shift 2
            ;;
        --health-check)
            HEALTH_CHECK=true
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
            log_error "Unknown argument: $1"
            usage
            ;;
    esac
done

if [[ -z "${TARGET}" ]]; then
    log_error "Rollback target is required (railway|alembic|docker|full)"
    exit 1
fi

# Validate service
case "${SERVICE}" in
    api|dashboard|all) ;;
    *)
        log_error "Invalid service: ${SERVICE}. Must be: api, dashboard, all"
        exit 1
        ;;
esac

# Validate revisions is a positive integer
if ! [[ "${REVISIONS}" =~ ^[0-9]+$ ]] || [[ "${REVISIONS}" -eq 0 ]]; then
    log_error "Revisions must be a positive integer, got: ${REVISIONS}"
    exit 1
fi

# ===========================================================================
# Rollback functions
# ===========================================================================

rollback_railway() {
    log_info "--- Railway Rollback ---"

    if ! command -v railway &>/dev/null; then
        log_error "Railway CLI not found. Install: npm i -g @railway/cli"
        return 1
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        log_info "[DRY RUN] Would redeploy previous Railway deployment for service: ${SERVICE}"
        return 0
    fi

    if [[ "${SERVICE}" == "all" || "${SERVICE}" == "api" ]]; then
        log_info "Rolling back API service..."
        # Get previous deployment ID
        local prev_deploy
        prev_deploy=$(railway deployments list --json 2>/dev/null \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[1]['id'] if len(d)>1 else '')" 2>/dev/null \
            || echo "")

        if [[ -n "${prev_deploy}" ]]; then
            railway redeploy --deployment "${prev_deploy}" --yes 2>/dev/null \
                || log_warn "Railway API redeploy failed — try manual: railway redeploy"
        else
            log_warn "Could not determine previous deployment — attempting git rollback"
            railway up --detach 2>/dev/null || true
        fi
    fi

    if [[ "${SERVICE}" == "all" || "${SERVICE}" == "dashboard" ]]; then
        log_info "Rolling back Dashboard service..."
        railway up --detach --service dashboard --path-as-root dashboard 2>/dev/null \
            || log_warn "Railway dashboard redeploy failed"
    fi
}

rollback_alembic() {
    log_info "--- Alembic Rollback (${REVISIONS} revision(s)) ---"

    if [[ -z "${DATABASE_URL:-}" ]]; then
        log_error "DATABASE_URL is required for Alembic rollback"
        return 1
    fi

    local PYTHON_CMD
    PYTHON_CMD=$(command -v python3 || command -v python || echo "")
    if [[ -z "${PYTHON_CMD}" ]]; then
        log_error "Python not found"
        return 1
    fi

    # Show current version
    local current_ver
    current_ver=$("${PYTHON_CMD}" -m alembic current 2>/dev/null | head -1 || echo "unknown")
    log_info "Current Alembic version: ${current_ver}"

    if [[ "${DRY_RUN}" == true ]]; then
        # Show what would be downgraded
        log_info "[DRY RUN] Would downgrade ${REVISIONS} revision(s)"
        "${PYTHON_CMD}" -m alembic history -r "-${REVISIONS}:current" 2>/dev/null || true
        return 0
    fi

    # Stop services that use the database
    log_info "Stopping API services before schema downgrade..."
    if command -v docker &>/dev/null; then
        docker compose -f "${COMPOSE_FILE}" stop api worker 2>/dev/null || true
    fi

    # Perform downgrade
    log_info "Downgrading ${REVISIONS} revision(s)..."
    "${PYTHON_CMD}" -m alembic downgrade "-${REVISIONS}"

    # Show new version
    local new_ver
    new_ver=$("${PYTHON_CMD}" -m alembic current 2>/dev/null | head -1 || echo "unknown")
    log_info "New Alembic version: ${new_ver}"

    # Restart services
    if command -v docker &>/dev/null; then
        docker compose -f "${COMPOSE_FILE}" start api worker 2>/dev/null || true
    fi
}

rollback_docker() {
    log_info "--- Docker Rollback ---"

    if ! command -v docker &>/dev/null; then
        log_error "Docker not found"
        return 1
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        if [[ -n "${DOCKER_TAG}" ]]; then
            log_info "[DRY RUN] Would rollback to Docker tag: ${DOCKER_TAG}"
        else
            log_info "[DRY RUN] Would rollback to previous git commit (HEAD~1)"
        fi
        return 0
    fi

    local compose_cmd="docker compose"
    if ! ${compose_cmd} version &>/dev/null 2>&1; then
        compose_cmd="docker-compose"
    fi

    if [[ -n "${DOCKER_TAG}" ]]; then
        # Roll back to specific tag
        log_info "Rolling back to tag: ${DOCKER_TAG}"
        ${compose_cmd} -f "${COMPOSE_FILE}" down --remove-orphans 2>/dev/null || true

        if [[ "${SERVICE}" == "all" || "${SERVICE}" == "api" ]]; then
            docker pull "ghcr.io/mas/api:${DOCKER_TAG}" 2>/dev/null || true
        fi
        if [[ "${SERVICE}" == "all" || "${SERVICE}" == "dashboard" ]]; then
            docker pull "ghcr.io/mas/dashboard:${DOCKER_TAG}" 2>/dev/null || true
        fi

        ${compose_cmd} -f "${COMPOSE_FILE}" up -d --remove-orphans
    else
        # Git-based rollback (deploy-spec quick rollback)
        log_info "Rolling back via git checkout HEAD~1..."

        cd "${PROJECT_DIR}" 2>/dev/null || cd "$(git rev-parse --show-toplevel)" || {
            log_error "Cannot find project directory"
            return 1
        }

        local current_sha
        current_sha=$(git rev-parse --short HEAD)
        log_info "Current commit: ${current_sha}"

        ${compose_cmd} -f "${COMPOSE_FILE}" down --remove-orphans 2>/dev/null || true

        git checkout HEAD~1 2>/dev/null || {
            log_error "git checkout HEAD~1 failed"
            return 1
        }

        local rollback_sha
        rollback_sha=$(git rev-parse --short HEAD)
        log_info "Rolled back to: ${rollback_sha}"

        ${compose_cmd} -f "${COMPOSE_FILE}" up -d --remove-orphans
    fi
}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

do_health_check() {
    log_info "--- Health Check ---"

    local max_retries=30
    local retry=0
    local all_ok=true

    if [[ "${SERVICE}" == "all" || "${SERVICE}" == "api" ]]; then
        log_info "Checking API: ${API_URL}/health"
        retry=0
        until curl -sf "${API_URL}/health" > /dev/null 2>&1; do
            retry=$((retry + 1))
            if [[ ${retry} -ge ${max_retries} ]]; then
                log_error "API health check failed after ${max_retries} attempts"
                all_ok=false
                break
            fi
            sleep 2
        done
        if [[ ${retry} -lt ${max_retries} ]]; then
            log_info "  API: healthy (${retry} retries)"
        fi
    fi

    if [[ "${SERVICE}" == "all" || "${SERVICE}" == "dashboard" ]]; then
        log_info "Checking Dashboard: ${DASHBOARD_URL}"
        retry=0
        until curl -sf "${DASHBOARD_URL}" > /dev/null 2>&1; do
            retry=$((retry + 1))
            if [[ ${retry} -ge ${max_retries} ]]; then
                log_warn "Dashboard health check failed after ${max_retries} attempts"
                all_ok=false
                break
            fi
            sleep 2
        done
        if [[ ${retry} -lt ${max_retries} ]]; then
            log_info "  Dashboard: healthy (${retry} retries)"
        fi
    fi

    if [[ "${all_ok}" != true ]]; then
        log_error "Some health checks FAILED"
        return 1
    fi
    log_info "All health checks passed"
}

# ===========================================================================
# Execute rollback
# ===========================================================================

log_info "=== MAS Rollback ==="
log_info "Target:    ${TARGET}"
log_info "Service:   ${SERVICE}"
log_info "Dry run:   ${DRY_RUN}"

case "${TARGET}" in
    railway)
        rollback_railway
        ;;
    alembic)
        rollback_alembic
        ;;
    docker)
        rollback_docker
        ;;
    full)
        log_info "Full rollback: docker -> alembic -> redeploy"
        rollback_docker
        rollback_alembic
        rollback_docker  # Second pass to bring services up with rolled-back schema
        ;;
esac

if [[ "${HEALTH_CHECK}" == true ]] && [[ "${DRY_RUN}" != true ]]; then
    do_health_check
fi

log_info "=== Rollback Complete ==="
