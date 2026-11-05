# Performance benchmarks

Benchmarks measure the work OCS does per message, with external calls faked, so one set of scenarios serves one-off checks, hill-climbing and regression tracking.

## What is faked

`apps/benchmarks/fakes.py` replaces the provider client at `LlmProvider.get_llm_service`. Prompt assembly, token counting, history loading and LangGraph execution stay on the measured path. Postgres and Redis are real.

| Helper | Use |
| --- | --- |
| `zero_latency()` | Canned instant reply. Default for hill-climbing. |
| `recorded_replay(responses)` | Replays captured provider responses (text, tool calls, chunks). |
| `fixed_delay(seconds)` | Constant latency, for concurrency and pool-pressure scenarios. |

## Running

```shell
# timing benchmarks (deselected by default)
uv run pytest -m bench apps/benchmarks

# save a baseline, change code, compare
uv run pytest -m bench apps/benchmarks --benchmark-save=before
uv run pytest -m bench apps/benchmarks --benchmark-compare=before
```

Compare results from the same machine only. Check the IQR before trusting a small delta: a spread that is large relative to the median means the run is too noisy.

Timing benchmarks share the settings in `apps.benchmarks.runner.TIMING` (warm-up, at least 5 seconds per benchmark, GC off during measured rounds). The marker takes precedence over the equivalent `--benchmark-*` command-line options.

To compare two commits, run both on the same machine in one session. `apps/benchmarks/ab.py` alternates runs of two checkouts and reports the B/A ratio of the medians per benchmark:

```shell
git worktree add ../ocs-base main
uv run python apps/benchmarks/ab.py --a ../ocs-base --b . --pairs 2
```

DB query-count ceilings (`apps/benchmarks/test_query_counts.py`) are ordinary tests and run with the normal suite. They do not depend on hardware, so any increase fails. Raise a ceiling in the same PR that legitimately adds queries.

## Nightly runs

The benchmarks run nightly against `main` on a dedicated EC2 runner, and can be run on demand against any branch. The runner, workflow and result history live in the private repository [`dimagi-internal/ocs-benchmarks`](https://github.com/dimagi-internal/ocs-benchmarks), so pull requests from forks cannot run code on the runner. Timings from the runner are only comparable with other runs on the runner.

## Pull request smoke check

The `Benchmark smoke check` workflow (`.github/workflows/benchmark_smoke.yml`) runs on pull requests that touch code. It checks out the pull request and its base branch, runs N5, P1 (typical history), P2 and P5 on both in one job with `ab.py` (3 pairs, alternating order), and fails when any of them is more than 25% slower than on the base, or when a benchmark ran on only one side. A pull request that renames a selected benchmark must update the `-k` expression in the workflow. It runs on a GitHub-hosted runner, which is too noisy to catch anything smaller; the nightly runs track small changes.

If the check fails on a change that should not slow anything down, re-run it first: a single run on a shared runner can be off by 10-15%. To reproduce locally, use the `ab.py` command in the workflow with the base branch checked out in a worktree.

## Adding a scenario

Build data in `apps/benchmarks/scenarios.py` (seeded, so every run sees the same rows), then add a timing test under `-m bench` and, if it is a pipeline scenario, a query-count ceiling.

## Not yet built

H3 never compresses its history, so every round sees the same 300 messages; compression is H4.

The scenario IDs implemented so far are N1, N5, N6, P1, P2, P5 and H1 to H3. The remaining scenarios and the channel and Locust layer from the benchmark plan are still to do.
