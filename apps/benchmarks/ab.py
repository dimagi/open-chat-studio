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

    summary = compare(
        [_medians(path) for path in runs["a"]],
        [_medians(path) for path in runs["b"]],
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(format_markdown(summary, a=_commit(runs["a"][0]), b=_commit(runs["b"][0])))
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True, help="baseline checkout")
    parser.add_argument("--b", required=True, help="candidate checkout")
    parser.add_argument("--pairs", type=_positive_int, default=2, help="number of A/B pairs (default 2)")
    parser.add_argument("--out", default="ab-results", help="directory for the per-run JSON and summary.json")
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
    env = {**os.environ, "DJANGO_DATABASE_NAME": f"ocs_bench_{side}"}
    print(f"[{side}] {checkout}: {' '.join(command)}", file=sys.stderr, flush=True)
    # pytest's output goes to stderr so stdout carries only the report.
    subprocess.run(command, cwd=checkout, env=env, check=True, stdout=sys.stderr)


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
        pair_ratios = [b_value / a_value for a_value, b_value in zip(a, b, strict=False)]
        rows.append(
            {
                "name": name,
                "a_median": statistics.median(a),
                "b_median": statistics.median(b),
                "ratio": statistics.median(b) / statistics.median(a),
                "pair_ratio_spread": max(pair_ratios) - min(pair_ratios),
            }
        )
    return rows


def format_markdown(rows: list[dict], a: str, b: str) -> str:
    lines = [
        f"A/B comparison: A = `{a}`, B = `{b}`",
        "",
        "| Benchmark | A median (ms) | B median (ms) | B/A | Pair spread |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        name = row["name"].split("::", 1)[-1]
        lines.append(
            f"| `{name}` | {row['a_median'] * 1000:.3f} | {row['b_median'] * 1000:.3f} "
            f"| {row['ratio']:.3f} | {row['pair_ratio_spread'] * 100:.1f}% |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
