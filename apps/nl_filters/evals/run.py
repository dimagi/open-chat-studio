import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from apps.nl_filters.agent import NLFilterAgent
from apps.nl_filters.evals.test_cases import TEST_CASES


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    input_query: str
    expected: str | list
    actual: str
    latency_ms: float
    error: str | None = None


def run_eval(agent, test_case: dict) -> EvalResult:
    """Run a single test case."""
    start = time.time()
    try:
        result = agent.translate(test_case["input"], table_type="sessions")
        latency_ms = (time.time() - start) * 1000
        actual = result["filter_query_string"]

        if "expected_filter" in test_case:
            passed = actual == test_case["expected_filter"]
        elif "expected_filter_contains" in test_case:
            passed = all(term.lower() in actual.lower() for term in test_case["expected_filter_contains"])
        else:
            passed = False

        return EvalResult(
            case_id=test_case["id"],
            passed=passed,
            input_query=test_case["input"],
            expected=test_case.get("expected_filter") or test_case.get("expected_filter_contains"),
            actual=actual,
            latency_ms=latency_ms,
        )
    except Exception as e:
        return EvalResult(
            case_id=test_case["id"],
            passed=False,
            input_query=test_case["input"],
            expected=test_case.get("expected_filter", ""),
            actual="",
            latency_ms=(time.time() - start) * 1000,
            error=str(e),
        )


def main():
    parser = argparse.ArgumentParser(description="Run Magic Filter evals")
    parser.add_argument("--category", type=str, help="Filter by category")
    parser.add_argument("--save", action="store_true", help="Save results to JSON")
    args = parser.parse_args()

    agent = NLFilterAgent()

    cases = TEST_CASES
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
        print(f"Category: {args.category}")
    print(f"Running {len(cases)} test cases\n")

    results = []
    for case in cases:
        result = run_eval(agent, case)
        results.append(result)
        status = "OK" if result.passed else "FAIL"
        print(f"  {status} {result.case_id}: {result.latency_ms:.0f}ms")
        if not result.passed:
            if result.error:
                print(f"    Error: {result.error}")
            else:
                print(f"    Expected: {result.expected}")
                print(f"    Actual: {result.actual}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    summary = {
        "timestamp": datetime.now().isoformat(),
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "pass_rate": passed / total if total > 0 else 0,
        "avg_latency_ms": sum(r.latency_ms for r in results) / total if total > 0 else 0,
        "results": [asdict(r) for r in results],
    }

    print(f"\n{'=' * 50}")
    print(f"Pass rate: {summary['pass_rate']:.1%} ({passed}/{total})")
    print(f"Avg latency: {summary['avg_latency_ms']:.0f}ms\n")

    if args.save:
        output_dir = Path(__file__).parent / "results"
        output_dir.mkdir(exist_ok=True)
        filename = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_dir / filename, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Saved to {output_dir / filename}\n")


if __name__ == "__main__":
    main()
