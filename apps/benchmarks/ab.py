"""Run the timing benchmarks for two checkouts in alternating order and report the B/A ratio per benchmark.

Runs on one machine in one session are closer to each other than runs on different days, so comparing
two commits this way cancels most of the noise from host and boot differences. Pairs alternate their
order (A B, B A, ...) so a drift during the session does not favour one side.

Usage (from the B checkout):

    uv run python apps/benchmarks/ab.py --a ../ocs-base --b . --pairs 2 --out ab-results

Each side gets its own test database, so the two checkouts can have different migrations.
Standard library only: it runs before either checkout's Django settings are loaded.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

PYTEST_ARGS = ["-m", "bench", "apps/benchmarks", "-p", "no:randomly", "-q"]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checkouts = {"a": Path(args.a).resolve(), "b": Path(args.b).resolve()}

    # The first run on a new test database is several percent slower on some benchmarks, so each side
    # runs once before the measured pairs and that result is discarded.
    for side in ("a", "b"):
        _run_side(checkouts[side], side, out / f"{side}-warmup.json", create_db=True, extra=args.pytest_args)

    runs: dict[str, list[Path]] = {"a": [], "b": []}
    for pair in range(args.pairs):
        order = ("a", "b") if pair % 2 == 0 else ("b", "a")
        for side in order:
            result = out / f"{side}-{len(runs[side]) + 1}.json"
            _run_side(checkouts[side], side, result, create_db=False, extra=args.pytest_args)
            runs[side].append(result)

    a_runs = [_medians(path) for path in runs["a"]]
    b_runs = [_medians(path) for path in runs["b"]]
    summary = compare(a_runs, b_runs)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(format_markdown(summary, a=_commit(runs["a"][0]), b=_commit(runs["b"][0])))
    if args.fail_above is not None and _report_failures(summary, a_runs, b_runs, args.fail_above):
        return 1
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True, help="baseline checkout")
    parser.add_argument("--b", required=True, help="candidate checkout")
    parser.add_argument("--pairs", type=_positive_int, default=2, help="number of A/B pairs (default 2)")
    parser.add_argument("--out", default="ab-results", help="directory for the per-run JSON and summary.json")
    parser.add_argument(
        "--fail-above",
        type=float,
        help="exit with status 1 when any benchmark's B/A ratio is above this, e.g. 1.25 for 25%% slower",
    )
    parser.add_argument("pytest_args", nargs="*", help="extra pytest arguments, after --")
    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _run_side(checkout: Path, side: str, result: Path, create_db: bool, extra: list[str]) -> None:
    command = ["uv", "run", "pytest", *PYTEST_ARGS, f"--benchmark-json={result.resolve()}", *extra]
    if create_db:
        command.append("--create-db")
    # DJANGO_DATABASE_NAME covers checkouts that predate DJANGO_DATABASE_TEST_NAME and use the discrete
    # DJANGO_DATABASE_* variables.
    env = {
        **os.environ,
        "DJANGO_DATABASE_TEST_NAME": f"test_ocs_bench_{side}",
        "DJANGO_DATABASE_NAME": f"ocs_bench_{side}",
    }
    print(f"[{side}] {checkout}: {' '.join(command)}", file=sys.stderr, flush=True)
    # pytest's output goes to stderr so stdout carries only the report.
    subprocess.run(command, cwd=checkout, env=env, check=True, stdout=sys.stderr)


def _report_failures(summary: list[dict], a_runs, b_runs, limit: float) -> bool:
    """Annotate benchmarks that regressed or ran on only one side, and return whether there were any."""
    unmatched = unmatched_names(a_runs, b_runs)
    for name, side in unmatched:
        print(f"::error::{name.split('::', 1)[-1]} ran only on {side.upper()}", file=sys.stderr)
    slow = regressions(summary, limit)
    for row in slow:
        name = row["name"].split("::", 1)[-1]
        print(f"::error::{name} is {(row['ratio'] - 1) * 100:.0f}% slower than A", file=sys.stderr)
    return bool(slow or unmatched)


def _medians(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text())
    return {bench["fullname"]: bench["stats"]["median"] for bench in data["benchmarks"]}


def _commit(path: Path) -> str:
    return json.loads(path.read_text()).get("commit_info", {}).get("id", "")[:10]


def compare(a_runs: list[dict[str, float]], b_runs: list[dict[str, float]]) -> list[dict]:
    """Per benchmark: median of each side's run medians, their ratio, and the spread of per-pair ratios."""
    names = sorted(set().union(*a_runs) & set().union(*b_runs))
    rows = []
    for name in names:
        a = [run[name] for run in a_runs if name in run]
        b = [run[name] for run in b_runs if name in run]
        pair_ratios = [
            b_run[name] / a_run[name]
            for a_run, b_run in zip(a_runs, b_runs, strict=True)
            if name in a_run and name in b_run
        ]
        rows.append(
            {
                "name": name,
                "a_median": statistics.median(a),
                "b_median": statistics.median(b),
                "ratio": statistics.median(b) / statistics.median(a),
                "pair_ratio_spread": max(pair_ratios) - min(pair_ratios) if pair_ratios else None,
            }
        )
    return rows


def unmatched_names(a_runs: list[dict[str, float]], b_runs: list[dict[str, float]]) -> list[tuple[str, str]]:
    """The (name, side) pairs for benchmarks that ran on only one side, which ``compare`` drops."""
    a_names = set().union(*a_runs)
    b_names = set().union(*b_runs)
    return [(name, "a") for name in sorted(a_names - b_names)] + [(name, "b") for name in sorted(b_names - a_names)]


def regressions(rows: list[dict], limit: float) -> list[dict]:
    """The rows whose B/A ratio is above ``limit``."""
    return [row for row in rows if row["ratio"] > limit]


def format_markdown(rows: list[dict], a: str, b: str) -> str:
    lines = [
        f"A/B comparison: A = `{a}`, B = `{b}`",
        "",
        "| Benchmark | A median (ms) | B median (ms) | B/A | Pair spread |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        name = row["name"].split("::", 1)[-1]
        spread = "n/a" if row["pair_ratio_spread"] is None else f"{row['pair_ratio_spread'] * 100:.1f}%"
        lines.append(
            f"| `{name}` | {row['a_median'] * 1000:.3f} | {row['b_median'] * 1000:.3f} "
            f"| {row['ratio']:.3f} | {spread} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
