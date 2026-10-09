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

## Adding a scenario

Build data in `apps/benchmarks/scenarios.py` (seeded, so every run sees the same rows), then add a timing test under `-m bench` and, if it is a pipeline scenario, a query-count ceiling.

## Not yet built

H3 never compresses its history, so every round sees the same 300 messages; compression is H4.

The scenario IDs implemented so far are N1, N5, N6, P1, P2, P5 and H1 to H3. The remaining scenarios, the channel and Locust layer, the dedicated runner and nightly tracking from the benchmark plan are still to do.
