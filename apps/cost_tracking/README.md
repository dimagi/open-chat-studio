# Cost Tracking App

This app records the cost of every LLM call OCS makes and surfaces it to the team that owns the chat. The whole feature is gated by the `flag_ai_cost_monitoring` team-scoped Waffle flag, so teams opt in.

There are two halves: the capture path (record what happened) and the resolution path (price what was recorded). A small operational layer (seed loader, auto-update workflow) keeps pricing data fresh.


## Data Model

Two models in `models.py`:

- `PricingRule` stores per-1K-token rates keyed by `(team, provider_type, model_name, service_kind)`. `team=NULL` means a global rule; a row with `team=<team>` is a team override. Effectively write-once: rate changes close the active rule via `effective_to=now()` and insert a fresh one. A partial unique constraint enforces "at most one active rule per key".
- `UsageRecord` (subclass of `BaseTeamModel`) is one row per `(trace, model, service_kind)` bucket. Snapshots `unit_price` and `currency` so historical rows are stable across rate changes. The `pricing_rule` FK uses `on_delete=PROTECT` so it remains the canonical "this row was priced" anchor.

`ServiceKind` covers `llm_input`, `llm_output`, `llm_cached_input`, `llm_cache_write`. `Confidence` is `EXACT` / `ESTIMATED` / `UNKNOWN` and tags each `UsageRecord` based on how the token count was obtained.

## Capture Path

The `OCSTracer` (in `apps/service_providers/tracing/`) collects `UsageEvent`s during a trace and calls `record_usage_bulk()` from `services/recorder.py` once at trace finalisation. Cost is computed as `(quantity / 1000) * unit_price`.

Provider identity is propagated via `model.metadata["ocs_provider_type"]` (stamped in `LlmService.get_chat_model` via a template method). The collector uses that to bucket usage by `(provider, model)`, so the same model name routed through different providers gets billed separately.

When `usage_metadata` is missing from the LangChain response, the collector falls back: `tiktoken` for the OpenAI family, `count_tokens_approximately` for everything else. Confidence is set to `ESTIMATED`. When there are no prompts to count either, the row is emitted as `UNKNOWN` with `extra["missing_usage_calls"]` so the dashboard's `unknown_call_count` flags the coverage gap.

## Resolution Path

`PricingResolver` in `services/pricing.py` resolves a `PricingKey` to a `ResolvedRule` at a given time. Team-scoped rules win over globals. Results are cached; `signals.py` busts the cache on every `PricingRule.save()` / `delete()`. Bulk reads from views use `_pricing_lookup` (in `apps/service_providers/views.py`) which does the same join in a single query.

## Seed Data and Updates

The canonical pricing seed lives in `seed_data/llm_pricing.json` (per-1K-tokens, one entry per `(provider_type, model_name)`). Migration `0002_seed_pricing.py` and any subsequent `NNNN_rate_update_*.py` migrations load it via `load_pricing_data()` from `migration_utils.py`, which calls the `load_ai_pricing` management command. The loader is idempotent and handles supersession on rate changes.

`.github/workflows/auto-update-models.yml` runs `scripts/reconcile_models.py` daily; one upstream pass produces three signals that the workflow acts on:

- `new_models` - candidates not yet in `default_models.py`. Claude Code opens a PR to register them following `docs/developer_guides/managing_models.md`.
- `price_changes` - upstream rates have moved for an existing seed entry. The script rewrites `llm_pricing.json` and emits a `NNNN_rate_update_YYYYMMDD.py` data migration; the workflow opens a mechanical "Pricing update" PR.
- `missing_pricing` - active OCS-managed models (entries in `DEFAULT_LLM_PROVIDER_MODELS`) with no `llm_input`/`llm_output` seed rule. The workflow opens or updates a tracked GitHub issue.

`backfill_pricing_seed` (in `management/commands/`) is a one-shot developer tool that walks `DEFAULT_LLM_PROVIDER_MODELS` and fills the seed from LiteLLM for any uncovered model.

## Surface

Two places consume the data, both gated by `flag_ai_cost_monitoring`:

- **Dashboard panel** (`templates/dashboard/_cost_tracking_panel.html`). Period spend, delta vs prior period, exact/estimated breakdown, top-N chatbots. Reacts to the dashboard date filter via `dashboard:api_cost_tracking_panel`.
- **LLM Provider page** shows each model's current per-1K rate inline. Admins can override at team scope via an HTMX modal (`pricing_override` view) or revert to global. The custom-model creation dialog accepts optional input/output rates that persist as team-scoped `PricingRule` rows in the same transaction as the model save.

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
    recorder.py             record_usage_bulk + UsageEvent / TraceContext
    estimation.py           tiktoken + response_text helpers
    reporting.py            cost_summary, top_n_bots, last_synced_at
```
