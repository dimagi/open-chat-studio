# Cost Tracking App

This app records the cost of every LLM call OCS makes and surfaces it to the team that owns the chat.

There are two halves: the capture path (record what happened) and the resolution path (price what was recorded). A small operational layer (seed loader, auto-update workflow) keeps pricing data fresh.


## Data Model

Two models in `models.py`:

- `PricingRule` stores per-1K-token rates keyed by `(team, provider_type, model_name, service_kind)`. `team=NULL` means a global rule; a row with `team=<team>` is a team override. Effectively write-once: rate changes close the active rule via `effective_to=now()` and insert a fresh one. A partial unique constraint enforces "at most one active rule per key".
- `UsageRecord` (subclass of `BaseTeamModel`) is one row per `(trace, model, service_kind)` bucket — `trace` is null for callers outside the tracer, currently evaluator judge calls. Snapshots `unit_price` and `currency` so historical rows are stable across rate changes. The `pricing_rule` FK uses `on_delete=PROTECT` so it remains the canonical "this row was priced" anchor.

`ServiceKind` covers `llm_input`, `llm_output`, `llm_cached_input`, `llm_cache_write`. `Confidence` is `EXACT` / `ESTIMATED` / `UNKNOWN` and tags each `UsageRecord` based on how the token count was obtained.

`UsageSource` is `chat` / `evaluation` and says what the spend was *for*. It is the only sanctioned way to tell the two apart — do not infer it from `trace_id` or from `experiment`/`session` being null, because judge rows from a generation run carry both. Eval rows also carry an `evaluation_config` FK (`SET_NULL`; the run id is in `extra`, since runs are pruned).

## Capture Path

The `OCSTracer` (in `apps/service_providers/tracing/`) collects `UsageEvent`s during a trace and calls `record_usage_bulk()` from `services/recorder.py` once at trace finalisation. Cost is computed as `(quantity / 1000) * unit_price`.

LLM calls outside the chat/pipeline path have no trace to drain, so they attach their own `MetricsCollector` and record directly. Evaluator (judge) calls do this via `track_evaluator_usage` in `apps/evaluations/usage.py`, writing rows with `source=evaluation`. The bot generation an eval run drives is ordinary traced traffic and needs nothing special — it is `source=chat`.

Provider identity is propagated via `model.metadata["ocs_provider_type"]` (stamped in `LlmService.get_chat_model` via a template method). The collector uses that to bucket usage by `(provider, model)`, so the same model name routed through different providers gets billed separately.

When `usage_metadata` is missing from the LangChain response, the collector falls back: `tiktoken` for the OpenAI family, `count_tokens_approximately` for everything else. Confidence is set to `ESTIMATED`. When there are no prompts to count either, the row is emitted as `UNKNOWN` with `extra["missing_usage_calls"]` so the dashboard's `unknown_call_count` flags the coverage gap.

## Resolution Path

Reads split on one rule ([ADR-0048](../../docs/adr/0048-evaluation-spend-is-team-spend-not-entity-spend.md)): **evaluation spend is the team's spend, but never a chatbot's, a participant's, or a conversation's.** Grouped per-entity reads (`costs_by_experiment`, `session_usage`, `usage_by_group`, `p95_cost_per_trace`) go through `_attributable_records` and count `chat` only, and `_scoped_records` applies the same restriction whenever a `CostFilters` narrows to specific chatbots/participants/platforms — a filtered read attributes cost just as much as a grouped one. Only an unfiltered, ungrouped read (`cost_summary`, `cost_total`, `token_counts`, `*_timeseries`, `coverage_gaps`) is a team total that counts every source. No user-facing surface breaks the total down by source yet — that belongs with cost breakdowns generally.

`PricingResolver` in `services/pricing.py` resolves a `PricingKey` to a `ResolvedRule` at a given time. Team-scoped rules win over globals. Results are cached; `signals.py` busts the cache on every `PricingRule.save()` / `delete()`. Bulk reads from views use `_pricing_lookup` (in `apps/service_providers/views.py`) which does the same join in a single query.

## Seed Data and Updates

The canonical pricing seed lives in `seed_data/llm_pricing.json` (per-1K-tokens, one entry per `(provider_type, model_name)`). Migration `0002_seed_pricing.py` and any subsequent `NNNN_rate_update_*.py` migrations load it via `load_pricing_data()` from `migration_utils.py`, which calls the `load_ai_pricing` management command. The loader is idempotent and handles supersession on rate changes.

`.github/workflows/auto-update-models.yml` runs daily. It is one job that invokes Claude Code, which runs `python3 -m scripts.auto_sync_models.sync` and acts on what it produced. The script does everything derivable from the data; Claude makes the judgement calls and checks the script's work. The script is a six-layer pipeline: `default_models.py` and `llm_pricing.json` are read into one mapping keyed by `(provider, model)`, LiteLLM's `model_prices_and_context_window.json` is translated into the same shape, and comparing the two yields every signal the workflow acts on:

- `added` - upstream serves it, we do not, we never deleted it, and we have not rejected it (anything still undecided from an earlier run is included). Claude registers it following `docs/developer_guides/managing_models.md`.
- `deprecated` - models still listed as active whose upstream `deprecation_date` has passed. Claude Code marks them `deprecated=True`.
- `repriced` / `backfilled` - a rate that has moved upstream, or one the seed lacks entirely. The script rewrites `llm_pricing.json`; Claude checks the diff against the providers' own pricing pages and writes the migration that loads it. Rows with no `default_models.py` entry are carried through untouched, so a `DELETED_MODELS` entry keeps the price its historical usage is costed against.
- `unpriced` - active models the seed cannot cost and LiteLLM cannot fill. Listed under `## Needs follow-up` in the PR description.
- `removed` - models LiteLLM no longer lists at all. Claude checks each against the provider's own docs and retires only the ones that really are gone. A run proposing to retire more than a quarter of the catalogue, or reading a price table under 300 models, is treated as an upstream fault and exits without acting.

`scripts/auto_sync_models/model_ledger.json` is the pipeline's only memory: it holds the models seen upstream and not registered, each with its verdict and the parameter-relevant capability flags LiteLLM reports. Only a `rejected` verdict takes a model off the list, so anything left undecided is offered again on the next run; registering a model deletes its entry, leaving `default_models.py` as the single record that OCS serves it. The script records each candidate as `pending`; Claude closes every one out in the same run, so a verdict is never left hanging.

Rates are resolved per provider, from each provider's own price-table key: Azure resells OpenAI models at its own rate, so one rate copied across providers bills the rest wrong.

`backfill_pricing_seed` (in `management/commands/`) is a one-shot developer tool that walks `DEFAULT_LLM_PROVIDER_MODELS` and fills the seed from LiteLLM for any uncovered model.

## Surface

Every surface reads through `services/reporting.py`; none of them are gated.

- **Dashboard panel** (`templates/dashboard/_cost_tracking_panel.html`). Period spend, delta vs prior period, exact/estimated breakdown, top-N chatbots. Reacts to the dashboard date filter via `dashboard:api_cost_tracking_panel`. The Bot Performance and Most Active Participants tables on the same page carry per-entity cost columns.
- **LLM Provider page** shows each model's current per-1K rate inline. Admins can override at team scope via an HTMX modal (`pricing_override` view) or revert to global. The custom-model creation dialog accepts optional input/output rates that persist as team-scoped `PricingRule` rows in the same transaction as the model save.
- **Chatbot home** shows a 30-day spend / sessions / messages widget (`get_latest_chatbot_usage_summary`).
- **Session detail** shows the session's tokens and cost, via `session_usage`. Team members only - a participant viewing their own session does not see spend.
- **Participants table** carries a 30-day cost column, computed one page at a time (`UsageRecord` has no `(team, participant)` index, so the column is not sortable).
- **Trace detail** shows per-model tokens and cost for the trace, via `trace_token_usage`.
- **Evaluations UI** (`apps/evaluations/views/evaluation_config_views.py`): a Cost column on the run list, a per-evaluator/per-model breakdown on the run detail page, and a last-30-days/all-time summary on the config page. Reads `evaluation_run_cost`/`evaluation_run_costs`/`evaluation_config_cost_summary` below — the dedicated evaluation-scoped path, since the team-scoped reads above deliberately exclude evaluation spend from per-entity reads (ADR-0048).

The chatbot home, session detail and trace detail widgets render the shared `templates/cost_tracking/_usage_summary.html`, so the cost figure and its confidence badge read identically across the three.


## Layout

```text
apps/cost_tracking/
  models.py                 PricingRule, UsageRecord, ServiceKind, Confidence
  signals.py                Cache invalidation on PricingRule mutations
  admin.py                  Django admin (PricingRule edit, UsageRecord read-only)
  management/commands/
    load_ai_pricing.py      Idempotent seed loader, called from migrations
    backfill_pricing_seed.py  One-shot bulk filler from LiteLLM
  migration_utils.py        load_pricing_data() factory for data migrations
  seed_data/llm_pricing.json
  services/
    pricing.py              PricingResolver + cache
    recorder.py             record_usage_bulk + UsageEvent / UsageContext
    estimation.py           tiktoken + response_text helpers
    reporting.py            cost_summary, costs_by_experiment, coverage_gaps, cost_timeseries,
                             evaluation_run_cost, evaluation_run_costs, evaluation_config_cost_summary
```
