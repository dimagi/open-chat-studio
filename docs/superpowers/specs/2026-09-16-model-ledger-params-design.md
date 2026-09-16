---
status: active
---

# Model ledger as the record of undecided and declined models — Design

## Summary

`scripts/auto_sync_models/model_ledger.json` currently suppresses a model from
rediscovery by the *presence* of its key, regardless of the verdict stored
against it. This strands any model left `pending`: it is reported as backlog
forever and never re-offered. All 227 entries in the file reached that state
before being flipped to `rejected` by hand.

This design does three things:

1. Suppression becomes verdict-based. Only `rejected` models are subtracted, so
   a `pending` model is re-offered until someone decides it.
2. Each entry carries the model's parameter-relevant capability flags from
   LiteLLM, which the pipeline currently reads and discards.
3. `added` and `backlog` collapse into one list, because under (1) they are the
   same set.

The ledger's scope stays as it is today: models we have seen and not
registered. `default_models.py` remains the only record of what we do register.

## Problem

### What the ledger does today

Two reads, and no others anywhere in the codebase:

```python
sync.py:71  added   = live.keys() - ours.keys() - deleted - ledger.keys()
sync.py:72  backlog = [e for k, e in ledger.items() if e.verdict == PENDING and k not in ours]
```

Line 71 ignores the verdict entirely. Line 72 is the only place a verdict is
read, and it feeds a report. So `registered` and `rejected` are
indistinguishable to the pipeline — both are merely "not pending", and both
suppress identically.

### The failure this causes

Because presence alone suppresses, `pending` is terminal rather than a holding
state. A run that discovers candidates writes them as `pending`; if the job that
should decide them does not commit verdicts, they are suppressed forever with no
decision ever recorded. The only remedy is hand-editing the JSON.

This is not hypothetical. The committed ledger reached 227 entries, every one
`pending`, and `added` was 0 on every subsequent run as a result. They were
subsequently set to `rejected` by hand, which cleared the backlog report but —
since presence is what suppresses — did not make any of them re-offerable.

### Parameters are read and discarded

LiteLLM's table carries 43 `supports_*` keys. `translate()` reads exactly one of
them (`supported_output_modalities`, to drop audio-only models mistagged as
chat) and discards the rest. `ModelRecord` has no field for them, so nothing
downstream can see them.

Job 2 must choose a parameters class for each model it registers, and the
workflow prompt forbids it from re-fetching the upstream table. It therefore
makes that choice with no access to the flags that describe what the model
actually supports.

## Design

### What the ledger holds

Every model we have seen upstream and have not registered:

| Status | Meaning | Persisted because |
| --- | --- | --- |
| `pending` | awaiting a verdict; job 2's worklist | `first_seen` and `params` are not derivable |
| `rejected` | decided against, with a reason | the only truly irreplaceable state in the file |

Registered models are not in the ledger. `default_models.py` is the sole record
of what OCS registers, so the fact lives in exactly one place and cannot drift.
Job 2 therefore *deletes* an entry when it registers the model, rather than
marking it. `REGISTERED` stays defined in `records.py` but is no longer a value
the pipeline writes or reads.

### Entry shape

```json
"openai/gpt-5.7": {
  "first_seen": "2026-09-20",
  "verdict": "pending",
  "params": { "reasoning": true, "effort_levels": ["none", "xhigh"] }
}
```

A rejected entry adds `"reason"`, as today. `params` is optional: absent on the
227 existing entries, and absent on any model the table says nothing about.

### Which flags become `params`

Only those that map onto a field in `model_parameters.py`. The rest describe
capabilities OCS does not express as a parameter and are dropped.

| LiteLLM flag | Maps to |
| --- | --- |
| `supports_reasoning` | whether an `effort` field applies at all |
| `supports_{none,minimal,low,max,xhigh}_reasoning_effort` | members of the effort `TextChoices` enum |
| `supports_adaptive_thinking` | `adaptive_thinking` |
| `supports_sampling_params` | whether `temperature` / `top_p` apply |
| `supports_anthropic_thinking_payload`, `supports_legacy_thinking` | `thinking` / `budget_tokens` |

The five effort-level flags collapse into one `effort_levels` list holding the
levels the model accepts; `low`, `medium` and `high` are the unflagged baseline
and so are never listed.

A flag the table omits means *unknown*, not *false*, so only keys actually
present upstream are recorded and a present `false` is kept.
`supports_sampling_params: false` is the case that forces this — it is what
tells us a model refuses `temperature`, and dropping it would lose the signal.
Models carrying none of these keys get no `params` block at all, which is most
of them.

Spot-checked against the current catalogue, these reproduce the hand-assigned
classes exactly: `gpt-5.2` flags `none`+`xhigh` against
`GPT52ReasoningEffortParameter` (none/low/medium/high/xhigh); `claude-opus-4-7`
flags `max`+`xhigh` with `sampling=False` against `Claude47EffortParameter` plus
a class that omits `temperature`; `gpt-4.1-mini` flags nothing against
`BasicParameters`.

### Suppression becomes verdict-based

```python
# before
added = live.keys() - ours.keys() - deleted - ledger.keys()

# after
added = live.keys() - ours.keys() - deleted - rejected_keys(ledger)
```

`pending` is deliberately not subtracted. Writing candidates back to the ledger
stays idempotent — an existing entry keeps its original `first_seen`.

### `added` and `backlog` collapse

With pending models included in `added`, `backlog` becomes a subset of it and
reporting both is redundant. `Diff.backlog` and the payload's `backlog[]` are
removed. Long-undecided candidates are visible through `first_seen` on the
`added` entries.

### `reconciliation.json`

Unchanged in role: job 1 writes it before job 2 acts, so it describes what
*should* happen, not what did. It carries job 2's worklist plus signals that are
computed fresh each run and never live in the ledger — `repriced`, `backfilled`,
`unpriced` from the pricing seed comparison, and `deprecated`, `removed` from the
catalogue comparison. Each `added[]` entry gains the same `params` block.

### Known behaviour: pending models that leave upstream

A `pending` entry whose model no longer appears in `live` will not appear in
`added` and so will never be re-offered. It stays in the file as a record that
the model was once seen. This is a 0-case against the current data (every
pending key is still live upstream) and is left as-is rather than given
machinery.

### Known behaviour: `params` are recorded once, not refreshed

`advance_ledger` uses `setdefault`, so re-offering a pending model preserves its
`first_seen` and leaves its stored `params` as first seen. This does not affect
any decision: `added[]` in `reconciliation.json` is built from the live upstream
record, so job 2 always reads current flags. The ledger's copy is a record of
what we saw when the model was first offered.

## Out of scope

- **Params for registered models.** The ledger holds only unregistered models,
  so it cannot answer "what flags does `claude-opus-4-7` have".
- **Drift detection.** Following from the above, there is no stored record to
  compare a registered model's current flags against, so a model whose upstream
  flags diverge from its assigned parameters class will not be flagged.
- **Backfilling `params` onto the 227 rejected entries.** They are never
  reconsidered, so the data would not be read.
- **Parsing `parameters=` in the AST reader.** Not needed; nothing compares
  against the assigned class.

## Changes by file

| File | Change |
| --- | --- |
| `records.py` | `ModelRecord.params`; `LedgerEntry.params`; drop `Diff.backlog` |
| `upstream.py` | extract the flag subset in the record builder |
| `catalogue.py` | round-trip `params` through `read_ledger` / `write_ledger` |
| `sync.py` | verdict-based suppression in `compare()`; drop the backlog rule |
| `dispatch.py` | `params` into `_added_entry` and `advance_ledger`; drop `backlog` from `build_payload` |
| `.github/workflows/auto-update-models.yml` | header comment; job 2 prompt gains "use `params` when picking a parameters class" |
| `apps/cost_tracking/README.md` | the "offered exactly once" paragraph is no longer true |
| `test_auto_sync_models.py` | new coverage, below |

## Testing

TDD per `AGENTS.md`: tests first, then code to green. `pytest.mark.parametrize`
with readable `pytest.param(..., id=...)` per project convention.

- Flag extraction: a reasoning model, a non-reasoning model, one with every
  effort level, one with none. Assert a present `false` is kept and a flag OCS
  has no parameter for is dropped.
- Ledger round-trip: `params` survives read → write → read; entries without
  `params` round-trip unchanged and gain no key in the JSON.
- Suppression: a `rejected` key is excluded from `added`; a `pending` key is
  **included**; `first_seen` is preserved when a pending entry is rewritten.
- Payload: `added[]` carries `params`; `backlog` is gone from the payload and
  from the summary counts.

The existing 86 tests must stay green, with those asserting backlog behaviour
updated rather than deleted.

## Rollout

No migration. The 227 existing entries are already `rejected` and remain
suppressed under the new rule, so `added` stays 0 on the first run after this
lands — the change alters behaviour only for models discovered afterwards.
`params` is additive and optional, so the current file stays valid as written.
