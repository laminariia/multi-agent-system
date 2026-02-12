"""Validate Locust load test results against performance thresholds.

Reads Locust CSV output files and asserts:
  - P50 response time < 200ms for all endpoints
  - P95 response time < 500ms for all endpoints
  - P99 response time < 2000ms for all endpoints
  - Zero HTTP 5xx errors
  - Error rate < 1%
  - Minimum aggregate RPS > 50

Usage:
    python tests/load/validate_results.py results/load

    # Or after a Locust run:
    locust ... --csv results/load --headless
    python tests/load/validate_results.py results/load
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from conftest_load import (
    MAX_5XX_COUNT,
    MAX_ERROR_RATE_PCT,
    MIN_RPS,
    P50_THRESHOLD_MS,
    P95_THRESHOLD_MS,
    P99_THRESHOLD_MS,
)


@dataclass
class EndpointResult:
    name: str
    requests: int
    failures: int
    median_ms: float
    p95_ms: float
    p99_ms: float
    avg_ms: float
    max_ms: float
    rps: float
    error_rate: float


def parse_stats_csv(stats_path: Path) -> list[EndpointResult]:
    """Parse the Locust _stats.csv file into EndpointResult objects."""
    results = []
    with open(stats_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "")
            if not name or name == "Aggregated":
                continue

            requests = int(row.get("Request Count", 0) or 0)
            failures = int(row.get("Failure Count", 0) or 0)
            error_rate = (failures / requests * 100) if requests > 0 else 0

            results.append(EndpointResult(
                name=name,
                requests=requests,
                failures=failures,
                median_ms=float(row.get("50%", 0) or 0),
                p95_ms=float(row.get("95%", 0) or 0),
                p99_ms=float(row.get("99%", 0) or 0),
                avg_ms=float(row.get("Average Response Time", 0) or 0),
                max_ms=float(row.get("Max Response Time", 0) or 0),
                rps=float(row.get("Requests/s", 0) or 0),
                error_rate=error_rate,
            ))

    return results


def parse_aggregated(stats_path: Path) -> dict | None:
    """Extract the Aggregated row from the Locust _stats.csv."""
    with open(stats_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Name") == "Aggregated":
                return row
    return None


def parse_failures_csv(failures_path: Path) -> list[dict]:
    """Parse the Locust _failures.csv for 5xx errors."""
    failures = []
    if not failures_path.exists():
        return failures
    with open(failures_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            failures.append(row)
    return failures


def validate(csv_prefix: str) -> bool:
    """Run all validations and return True if all pass."""
    stats_path = Path(f"{csv_prefix}_stats.csv")
    failures_path = Path(f"{csv_prefix}_failures.csv")

    if not stats_path.exists():
        print(f"ERROR: Stats file not found: {stats_path}")
        return False

    results = parse_stats_csv(stats_path)
    failures = parse_failures_csv(failures_path)
    aggregated = parse_aggregated(stats_path)

    if not results:
        print("ERROR: No endpoint results found in CSV")
        return False

    all_passed = True
    print("=" * 70)
    print("LOAD TEST VALIDATION REPORT")
    print("=" * 70)

    # 1. P50 latency check
    print(f"\n--- P50 Latency (threshold: {P50_THRESHOLD_MS}ms) ---")
    for r in results:
        status = "PASS" if r.median_ms <= P50_THRESHOLD_MS else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] {r.name}: P50={r.median_ms:.0f}ms")

    # 2. P95 latency check
    print(f"\n--- P95 Latency (threshold: {P95_THRESHOLD_MS}ms) ---")
    for r in results:
        status = "PASS" if r.p95_ms <= P95_THRESHOLD_MS else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] {r.name}: P95={r.p95_ms:.0f}ms (P50={r.median_ms:.0f}ms, avg={r.avg_ms:.0f}ms)")

    # 3. P99 latency check
    print(f"\n--- P99 Latency (threshold: {P99_THRESHOLD_MS}ms) ---")
    for r in results:
        status = "PASS" if r.p99_ms <= P99_THRESHOLD_MS else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] {r.name}: P99={r.p99_ms:.0f}ms")

    # 4. Error rate check
    print(f"\n--- Error Rate (threshold: {MAX_ERROR_RATE_PCT}%) ---")
    for r in results:
        if r.requests == 0:
            continue
        status = "PASS" if r.error_rate <= MAX_ERROR_RATE_PCT else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] {r.name}: {r.error_rate:.2f}% ({r.failures}/{r.requests})")

    # 5. 5xx error check
    print(f"\n--- 5xx Errors (threshold: {MAX_5XX_COUNT}) ---")
    server_errors = [
        f for f in failures
        if any(code in str(f.get("Error", "")) for code in ("500", "502", "503", "504"))
    ]
    if server_errors:
        all_passed = False
        for err in server_errors:
            print(f"  [FAIL] {err.get('Name', 'unknown')}: {err.get('Error', '')}")
    else:
        print("  [PASS] No 5xx errors")

    # 6. Minimum RPS check
    print(f"\n--- Minimum RPS (threshold: {MIN_RPS}) ---")
    if aggregated:
        total_rps = float(aggregated.get("Requests/s", 0) or 0)
        status = "PASS" if total_rps >= MIN_RPS else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] Aggregate RPS: {total_rps:.1f}")
    else:
        total_rps = sum(r.rps for r in results)
        status = "PASS" if total_rps >= MIN_RPS else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] Sum of endpoint RPS: {total_rps:.1f}")

    # Summary
    print("\n" + "=" * 70)
    total_requests = sum(r.requests for r in results)
    total_failures = sum(r.failures for r in results)
    overall_error_rate = (total_failures / total_requests * 100) if total_requests > 0 else 0
    worst_p50 = max((r.median_ms for r in results), default=0)
    worst_p95 = max((r.p95_ms for r in results), default=0)
    worst_p99 = max((r.p99_ms for r in results), default=0)
    print(f"Total requests: {total_requests}")
    print(f"Total failures: {total_failures} ({overall_error_rate:.2f}%)")
    print(f"Worst P50: {worst_p50:.0f}ms | Worst P95: {worst_p95:.0f}ms | Worst P99: {worst_p99:.0f}ms")
    print(f"\nOverall: {'PASS' if all_passed else 'FAIL'}")
    print("=" * 70)

    return all_passed


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <csv_prefix>")
        print(f"  Example: python {sys.argv[0]} results/load")
        sys.exit(2)

    csv_prefix = sys.argv[1]
    passed = validate(csv_prefix)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
