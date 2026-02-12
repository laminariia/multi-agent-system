#!/usr/bin/env bash
set -euo pipefail

# MAS Pre-Deployment Checklist
# Usage: bash scripts/run_all_checks.sh
#
# Runs all quality gates locally before pushing to CI.
# Exit code 0 = all passed, non-zero = failure.

FAIL=0
PASS=0

run_check() {
    local name="$1"
    shift
    echo ""
    echo "--- [$name] ---"
    if "$@"; then
        echo "  PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $name"
        FAIL=$((FAIL + 1))
    fi
}

echo "=========================================="
echo " MAS Pre-Deployment Checks"
echo " Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================="

# Set dummy env vars for tests
export DATABASE_URL="${DATABASE_URL:-postgresql://test:test@localhost:5432/test}"
export VALKEY_URL="${VALKEY_URL:-valkey://localhost:6379}"
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-test-key}"
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-ci-test-secret-key-min-32-chars-long}"
export ENCRYPTION_KEY="${ENCRYPTION_KEY:-ci-test-encryption-key}"

# 1. Ruff lint
run_check "Ruff Lint" ruff check src/ tests/

# 2. Ruff format
run_check "Ruff Format" ruff format --check src/ tests/

# 3. Unit tests with coverage gate
run_check "Unit Tests (coverage >= 70%)" pytest tests/unit/ \
    -x --timeout=120 -q \
    --cov=src \
    --cov-fail-under=70 \
    --cov-report=term-missing:skip-covered

# 4. YAML validation
run_check "YAML Validation" python -c "
import yaml, sys, pathlib
files = list(pathlib.Path('.').glob('**/*.yml')) + list(pathlib.Path('.').glob('**/*.yaml'))
files = [f for f in files if 'node_modules' not in str(f) and '.git/' not in str(f)]
errors = 0
for f in files:
    try:
        yaml.safe_load(f.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'  ERROR: {f}: {e}')
        errors += 1
print(f'  Checked {len(files)} YAML files, {errors} errors')
sys.exit(1 if errors else 0)
"

# 5. Gitleaks (if installed)
if command -v gitleaks &>/dev/null; then
    run_check "Gitleaks Secrets" gitleaks detect --source . --no-banner
else
    echo ""
    echo "--- [Gitleaks Secrets] ---"
    echo "  SKIP: gitleaks not installed (pip install gitleaks or brew install gitleaks)"
fi

# 6. Dashboard TypeScript (if node_modules present)
if [[ -d "dashboard/node_modules" ]]; then
    run_check "Dashboard TypeScript" bash -c 'cd dashboard && npx tsc --noEmit'
else
    echo ""
    echo "--- [Dashboard TypeScript] ---"
    echo "  SKIP: dashboard/node_modules not found (run: cd dashboard && npm ci)"
fi

# Summary
echo ""
echo "=========================================="
echo " Results: ${PASS} passed, ${FAIL} failed"
echo "=========================================="

if [[ "${FAIL}" -gt 0 ]]; then
    echo " Some checks failed. Fix issues before pushing."
    exit 1
fi

echo " All checks passed. Ready to push."
exit 0
