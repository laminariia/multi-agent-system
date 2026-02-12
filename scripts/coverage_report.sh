#!/usr/bin/env bash
set -euo pipefail

# MAS Local Coverage Report Generator
# Usage: bash scripts/coverage_report.sh [--open]
#
# Runs unit tests with coverage and generates an HTML report.
# Pass --open to automatically open the report in a browser.

OPEN_BROWSER=false
if [[ "${1:-}" == "--open" ]]; then
    OPEN_BROWSER=true
fi

echo "=== MAS Coverage Report ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Set dummy env vars for tests
export DATABASE_URL="${DATABASE_URL:-postgresql://test:test@localhost:5432/test}"
export VALKEY_URL="${VALKEY_URL:-valkey://localhost:6379}"
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-test-key}"
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-ci-test-secret-key-min-32-chars-long}"
export ENCRYPTION_KEY="${ENCRYPTION_KEY:-ci-test-encryption-key}"

echo "--- Running unit tests with coverage ---"
pytest tests/unit/ \
    -v --tb=short -x \
    --timeout=120 \
    --cov=src \
    --cov-fail-under=70 \
    --cov-report=html:htmlcov \
    --cov-report=term-missing:skip-covered \
    --cov-report=xml:coverage-unit.xml \
    --junitxml=junit-unit.xml

echo ""
echo "--- Coverage report generated ---"
echo "  HTML:  htmlcov/index.html"
echo "  XML:   coverage-unit.xml"
echo "  JUnit: junit-unit.xml"

if [[ "${OPEN_BROWSER}" == true ]]; then
    if command -v xdg-open &>/dev/null; then
        xdg-open htmlcov/index.html
    elif command -v open &>/dev/null; then
        open htmlcov/index.html
    elif command -v start &>/dev/null; then
        start htmlcov/index.html
    else
        echo "  (Could not detect browser opener — open htmlcov/index.html manually)"
    fi
fi

echo "=== Done ==="
