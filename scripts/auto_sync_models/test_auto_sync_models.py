"""Tests for the model/pricing reconciliation pipeline, one section per layer."""

import datetime
import json

import pytest

from scripts.auto_sync_models import upstream
from scripts.auto_sync_models.catalogue import (
    LEDGER_REL_PATH,
    load_seed,
    read_default_models,
    read_ledger,
    with_pricing,
    write_ledger,
)
from scripts.auto_sync_models.dispatch import (
    advance_ledger,
    apply_rate_changes,
    build_payload,
    generate_migration,
    github_outputs,
    render_missing_pricing_issue_body,
    render_pricing_pr_body,
)
from scripts.auto_sync_models.records import (
    PENDING,
    REJECTED,
    Diff,
    LedgerEntry,
    ModelRecord,
    PricingGap,
    RateChange,
)
from scripts.auto_sync_models.sync import compare

TODAY = datetime.date(2026, 9, 15)


def ours(provider, name, rates=None, deprecated=False, token_limit=1000):
    return ModelRecord(provider=provider, name=name, token_limit=token_limit, rates=rates or {}, deprecated=deprecated)


def theirs(provider, name, rates=None, deprecated=False, deprecation_date=None, source_key=None, params=None):
    return ModelRecord(
        provider=provider,
        name=name,
        rates=rates or {},
        deprecated=deprecated,
        deprecation_date=deprecation_date,
        source_key=source_key or f"{provider}/{name}",
        params=params or {},
    )


def catalogue(*records):
    return {record.key: record for record in records}


# Layer 1 - the OCS catalogue, parsed as AST
#
# The formatting cases below are deliberate: a comment between ``Model(`` and the
# name, a provider whose ``[`` sits on the next line, and a DELETED_MODELS tuple
# split over several lines all defeat a line-by-line parser but not a real one.

DEFAULT_MODELS_SOURCE = """
import dataclasses


@dataclasses.dataclass
class Model:
    name: str
    token_limit: int
    is_default: bool = False
    deprecated: bool = False
    replacement: str | None = None


def k(n: int) -> int:
    return n * 1024


DEFAULT_LLM_PROVIDER_MODELS = {
    "openai": [
        Model("gpt-4.1", 1000000, is_default=True),
        Model("gpt-3.5-turbo", k(16), deprecated=True, replacement="gpt-4.1"),
        Model(
            # a comment where a parser could lose the name
            "gpt-5.4",
            1050000,
        ),
    ],
    "anthropic":
        [
            Model("claude-opus-5", k(1000)),
        ],
}

DELETED_MODELS = [
    ("openai", "o1-preview"),
    ("openai", "gpt-4-turbo", "gpt-4.1"),
    (
        "anthropic",
        "claude-2.1",
    ),
]
"""

SEED_ROWS = [
    {
        "provider_type": "openai",
        "model_name": "gpt-4.1",
        "rules": [
            {"service_kind": "llm_input", "unit_price": "0.002"},
            {"service_kind": "llm_output", "unit_price": "0.008"},
        ],
    },
    {
        "provider_type": "openai",
        "model_name": "gpt-5.4",
        "rules": [{"service_kind": "llm_input", "unit_price": "0.0025"}],
    },
    {
        "provider_type": "anthropic",
        "model_name": "claude-2.1",
        "rules": [{"service_kind": "llm_input", "unit_price": "0.008"}],
    },
]


@pytest.fixture()
def repo_root(tmp_path):
    models = tmp_path / "apps" / "service_providers" / "llm_service"
    models.mkdir(parents=True)
    (models / "default_models.py").write_text(DEFAULT_MODELS_SOURCE)

    seed_dir = tmp_path / "apps" / "cost_tracking" / "seed_data"
    seed_dir.mkdir(parents=True)
    (seed_dir / "llm_pricing.json").write_text(json.dumps(SEED_ROWS))

    migrations = tmp_path / "apps" / "cost_tracking" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "0001_initial.py").write_text("")
    (migrations / "0009_add_rules.py").write_text("")
    (migrations / "__init__.py").write_text("")

    (tmp_path / "scripts" / "auto_sync_models").mkdir(parents=True)
    return tmp_path


def test_catalogue_reads_every_provider(repo_root):
    parsed, _ = read_default_models(repo_root)
    assert set(parsed) == {
        ("openai", "gpt-4.1"),
        ("openai", "gpt-3.5-turbo"),
        ("openai", "gpt-5.4"),
        ("anthropic", "claude-opus-5"),
    }


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        pytest.param(("openai", "gpt-4.1"), 1000000, id="int-literal"),
        pytest.param(("openai", "gpt-3.5-turbo"), 16 * 1024, id="k-helper"),
        pytest.param(("anthropic", "claude-opus-5"), 1000 * 1024, id="k-helper-on-continuation-line"),
        pytest.param(("openai", "gpt-5.4"), 1050000, id="name-behind-a-comment"),
    ],
)
def test_catalogue_reads_token_limits(repo_root, key, expected):
    parsed, _ = read_default_models(repo_root)
    assert parsed[key].token_limit == expected


def test_catalogue_reads_deprecation_and_replacement(repo_root):
    parsed, _ = read_default_models(repo_root)
    assert parsed[("openai", "gpt-3.5-turbo")].deprecated is True
    assert parsed[("openai", "gpt-3.5-turbo")].replacement == "gpt-4.1"
    assert parsed[("openai", "gpt-4.1")].deprecated is False


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(("openai", "o1-preview"), id="two-tuple"),
        pytest.param(("openai", "gpt-4-turbo"), id="three-tuple-with-replacement"),
        pytest.param(("anthropic", "claude-2.1"), id="tuple-split-over-lines"),
    ],
)
def test_deleted_models_are_kept_out_of_the_catalogue(repo_root, key):
    parsed, deleted = read_default_models(repo_root)
    assert key in deleted
    assert key not in parsed


# Layer 2 - pricing enrichment


def test_pricing_enrichment_attaches_rates(repo_root):
    parsed, _ = read_default_models(repo_root)
    priced, _ = with_pricing(parsed, load_seed(repo_root))
    assert priced[("openai", "gpt-4.1")].rates == {"llm_input": "0.002", "llm_output": "0.008"}


def test_pricing_enrichment_keeps_partial_rates(repo_root):
    parsed, _ = read_default_models(repo_root)
    priced, _ = with_pricing(parsed, load_seed(repo_root))
    assert priced[("openai", "gpt-5.4")].rates == {"llm_input": "0.0025"}


def test_pricing_enrichment_leaves_unpriced_models_empty(repo_root):
    parsed, _ = read_default_models(repo_root)
    priced, _ = with_pricing(parsed, load_seed(repo_root))
    assert priced[("anthropic", "claude-opus-5")].rates == {}


def test_pricing_enrichment_hands_back_rows_with_no_catalogue_entry(repo_root):
    """A DELETED_MODELS row keeps its price so historical usage stays costable."""
    parsed, _ = read_default_models(repo_root)
    _, orphans = with_pricing(parsed, load_seed(repo_root))
    assert [(row["provider_type"], row["model_name"]) for row in orphans] == [("anthropic", "claude-2.1")]


# Layer 3 - translating the LiteLLM price table


def entry(provider, mode="chat", **fields):
    return {"litellm_provider": provider, "mode": mode, **fields}


@pytest.mark.parametrize(
    ("key", "tag", "expected"),
    [
        pytest.param("gpt-4o", "openai", ("openai", "gpt-4o"), id="bare-key"),
        pytest.param("openai/gpt-4o", "openai", ("openai", "gpt-4o"), id="namespaced-key"),
        pytest.param("azure/eu/gpt-4o", "azure", ("azure", "gpt-4o"), id="azure-region-stripped"),
        pytest.param("gemini/gemini-2.5-pro", "gemini", ("google", "gemini-2.5-pro"), id="gemini-maps-to-google"),
        pytest.param(
            "vertex_ai/gemini-2.5-pro",
            "vertex_ai-language-models",
            ("google_vertex_ai", "gemini-2.5-pro"),
            id="vertex-language-tag",
        ),
        pytest.param(
            "vertex_ai/gemini-2.5-pro", "vertex_ai", ("google_vertex_ai", "gemini-2.5-pro"), id="vertex-plain-tag"
        ),
        pytest.param(
            "groq/openai/gpt-oss-120b", "groq", ("groq", "openai/gpt-oss-120b"), id="groq-keeps-vendor-segment"
        ),
        pytest.param(
            "perplexity/perplexity/sonar", "perplexity", ("perplexity", "sonar"), id="self-namespaced-key-unwrapped"
        ),
    ],
)
def test_translate_maps_keys_to_ocs_names(key, tag, expected):
    _, everything = upstream.translate({key: entry(tag)}, TODAY)
    assert set(everything) == {expected}


@pytest.mark.parametrize(
    ("key", "tag"),
    [
        pytest.param("perplexity/openai/gpt-5.1", "perplexity", id="cross-vendor-pass-through"),
        pytest.param("bedrock/claude-3", "bedrock", id="provider-ocs-does-not-support"),
    ],
)
def test_translate_rejects_keys_ocs_cannot_use(key, tag):
    _, everything = upstream.translate({key: entry(tag)}, TODAY)
    assert everything == {}


@pytest.mark.parametrize(
    ("fields", "in_live"),
    [
        pytest.param({"mode": "chat"}, True, id="chat"),
        pytest.param({"mode": "responses"}, True, id="responses"),
        pytest.param({"mode": "embedding"}, False, id="embedding"),
        pytest.param({"mode": "chat", "supported_output_modalities": ["text"]}, True, id="text-modality"),
        pytest.param({"mode": "chat", "supported_output_modalities": ["audio"]}, False, id="audio-only"),
    ],
)
def test_translate_live_holds_only_what_ocs_can_run(fields, in_live):
    live, everything = upstream.translate({"openai/m": {"litellm_provider": "openai", **fields}}, TODAY)
    assert (("openai", "m") in live) is in_live
    assert ("openai", "m") in everything


@pytest.mark.parametrize(
    ("date", "in_live", "deprecated"),
    [
        pytest.param("2026-09-14", False, True, id="date-passed"),
        pytest.param("2026-09-15", False, True, id="date-is-today"),
        pytest.param("2026-09-16", True, False, id="date-in-future"),
        pytest.param(None, True, False, id="no-date"),
        pytest.param("not-a-date", True, False, id="unparseable-date"),
    ],
)
def test_translate_applies_deprecation_dates(date, in_live, deprecated):
    live, everything = upstream.translate({"openai/m": entry("openai", deprecation_date=date)}, TODAY)
    assert (("openai", "m") in live) is in_live
    assert everything[("openai", "m")].deprecated is deprecated


def test_translate_prefers_a_namespaced_key_over_a_bare_one():
    """A reseller charges its own rate; the bare key holds the originator's."""
    data = {
        "gpt-4o": entry("openai", input_cost_per_token=0.0000025),
        "openai/gpt-4o": entry("openai", input_cost_per_token=0.000005),
    }
    _, everything = upstream.translate(data, TODAY)
    assert everything[("openai", "gpt-4o")].source_key == "openai/gpt-4o"


def test_translate_prefers_a_plain_key_over_a_regional_variant():
    """So the seed does not inherit a region's premium."""
    data = {
        "azure/eu/gpt-4o": entry("azure", input_cost_per_token=0.000009),
        "azure/gpt-4o": entry("azure", input_cost_per_token=0.0000025),
    }
    _, everything = upstream.translate(data, TODAY)
    assert everything[("azure", "gpt-4o")].source_key == "azure/gpt-4o"


@pytest.mark.parametrize(
    ("cost_field", "service_kind"),
    [
        pytest.param("input_cost_per_token", "llm_input", id="input"),
        pytest.param("output_cost_per_token", "llm_output", id="output"),
        pytest.param("cache_read_input_token_cost", "llm_cached_input", id="cached-input"),
        pytest.param("cache_creation_input_token_cost", "llm_cache_write", id="cache-write"),
    ],
)
def test_translate_converts_per_token_rates_to_per_1k(cost_field, service_kind):
    _, everything = upstream.translate({"openai/m": entry("openai", **{cost_field: 0.0000025})}, TODAY)
    assert everything[("openai", "m")].rates == {service_kind: "0.0025"}


def test_translate_omits_rates_the_table_does_not_carry():
    _, everything = upstream.translate({"openai/m": entry("openai")}, TODAY)
    assert everything[("openai", "m")].rates == {}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param({"max_input_tokens": 400000, "max_tokens": 128000}, 400000, id="input-tokens-preferred"),
        pytest.param({"max_tokens": 128000}, 128000, id="falls-back-to-max-tokens"),
        pytest.param({}, None, id="absent"),
        pytest.param({"max_input_tokens": 0}, None, id="zero-ignored"),
    ],
)
def test_translate_reads_the_token_limit(fields, expected):
    _, everything = upstream.translate({"openai/m": entry("openai", **fields)}, TODAY)
    assert everything[("openai", "m")].token_limit == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param({}, {}, id="no-flags-at-all"),
        pytest.param({"supports_vision": True}, {}, id="flag-ocs-has-no-parameter-for"),
        pytest.param({"supports_reasoning": True}, {"reasoning": True}, id="reasoning"),
        pytest.param({"supports_reasoning": False}, {"reasoning": False}, id="present-false-is-kept"),
        pytest.param({"supports_adaptive_thinking": True}, {"adaptive_thinking": True}, id="adaptive-thinking"),
        pytest.param({"supports_sampling_params": False}, {"sampling": False}, id="sampling-refused"),
        pytest.param(
            {"supports_anthropic_thinking_payload": True}, {"thinking_payload": True}, id="anthropic-thinking"
        ),
        pytest.param({"supports_legacy_thinking": True}, {"legacy_thinking": True}, id="legacy-thinking"),
        pytest.param(
            {"supports_none_reasoning_effort": True, "supports_xhigh_reasoning_effort": True},
            {"effort_levels": ["none", "xhigh"]},
            id="effort-levels",
        ),
        pytest.param(
            {"supports_xhigh_reasoning_effort": True, "supports_low_reasoning_effort": True},
            {"effort_levels": ["low", "xhigh"]},
            id="effort-levels-in-canonical-order",
        ),
        pytest.param(
            {"supports_max_reasoning_effort": False},
            {"effort_levels": []},
            id="effort-key-present-but-unsupported",
        ),
    ],
)
def test_translate_captures_the_flags_that_map_to_a_model_parameter(fields, expected):
    """LiteLLM carries 43 supports_* keys; only those OCS expresses as a parameter are kept."""
    _, everything = upstream.translate({"openai/m": entry("openai", **fields)}, TODAY)
    assert everything[("openai", "m")].params == expected


# Layer 4 - the ledger


def test_ledger_is_empty_when_the_file_is_absent(repo_root):
    assert read_ledger(repo_root) == {}


def test_ledger_round_trips(repo_root):
    entries = {
        ("openai", "gpt-9"): LedgerEntry("openai", "gpt-9", "2026-09-15", PENDING),
        ("azure", "gpt-3.5-turbo"): LedgerEntry("azure", "gpt-3.5-turbo", "2026-09-15", REJECTED, "superseded"),
    }
    write_ledger(repo_root, entries)
    assert read_ledger(repo_root) == entries


def test_ledger_round_trips_params(repo_root):
    entries = {
        ("openai", "gpt-9"): LedgerEntry(
            "openai", "gpt-9", "2026-09-15", PENDING, params={"reasoning": True, "effort_levels": ["low"]}
        )
    }
    write_ledger(repo_root, entries)
    assert read_ledger(repo_root) == entries


def test_ledger_omits_params_for_an_entry_that_has_none(repo_root):
    """The 227 entries written before this field existed must stay byte-identical."""
    write_ledger(repo_root, {("openai", "gpt-9"): LedgerEntry("openai", "gpt-9", "2026-09-15", PENDING)})
    assert "params" not in json.loads((repo_root / LEDGER_REL_PATH).read_text())["openai/gpt-9"]


def test_ledger_key_splits_on_the_first_slash_only(repo_root):
    """Groq model names carry a vendor segment (``openai/gpt-oss-120b``)."""
    write_ledger(repo_root, {("groq", "openai/gpt-oss-120b"): LedgerEntry("groq", "openai/gpt-oss-120b", "2026-09-15")})
    assert read_ledger(repo_root)[("groq", "openai/gpt-oss-120b")].model == "openai/gpt-oss-120b"


# Layer 5 - the comparison


def compare_with(ours_records=(), deleted=frozenset(), live_records=(), all_records=None, ledger=None):
    live = catalogue(*live_records)
    return compare(
        ours=catalogue(*ours_records),
        deleted=set(deleted),
        live=live,
        everything=catalogue(*all_records) if all_records is not None else dict(live),
        ledger=ledger or {},
    )


def test_added_is_upstream_minus_everything_we_know_about():
    diff = compare_with(
        ours_records=[ours("openai", "gpt-4.1")],
        deleted={("openai", "o1-preview")},
        live_records=[
            theirs("openai", "gpt-4.1"),
            theirs("openai", "o1-preview"),
            theirs("openai", "gpt-9"),
        ],
        ledger={("openai", "gpt-8"): LedgerEntry("openai", "gpt-8", "2026-01-01", REJECTED)},
    )
    assert [r.name for r in diff.added] == ["gpt-9"]


@pytest.mark.parametrize(
    ("verdict", "offered"),
    [
        pytest.param(PENDING, True, id="pending-is-re-offered-until-decided"),
        pytest.param(REJECTED, False, id="rejected-stays-suppressed"),
    ],
)
def test_only_a_rejected_model_is_kept_out_of_added(verdict, offered):
    """Presence used to suppress, which stranded anything left pending."""
    ledger = {("openai", "gpt-9"): LedgerEntry("openai", "gpt-9", "2026-01-01", verdict)}
    diff = compare_with(live_records=[theirs("openai", "gpt-9")], ledger=ledger)
    assert bool(diff.added) is offered


def test_a_pending_model_we_have_since_registered_is_not_offered_again():
    ledger = {("openai", "gpt-9"): LedgerEntry("openai", "gpt-9", "2026-01-01", PENDING)}
    diff = compare_with(ours_records=[ours("openai", "gpt-9")], live_records=[theirs("openai", "gpt-9")], ledger=ledger)
    assert diff.added == []


def test_removed_is_what_upstream_no_longer_lists_at_all():
    diff = compare_with(
        ours_records=[ours("openai", "gpt-4.1"), ours("openai", "gone")],
        live_records=[theirs("openai", "gpt-4.1")],
    )
    assert [r.name for r in diff.removed] == ["gone"]


def test_an_upstream_deprecated_model_is_not_also_reported_as_removed():
    """``removed`` compares against the unfiltered map, or every deprecation double-reports."""
    retired = theirs("openai", "old", deprecated=True, deprecation_date="2026-01-01")
    diff = compare_with(ours_records=[ours("openai", "old")], live_records=[], all_records=[retired])
    assert diff.removed == []
    assert [r.name for r in diff.deprecated] == ["old"]


def test_deprecation_skips_models_we_already_flagged():
    retired = theirs("openai", "old", deprecated=True, deprecation_date="2026-01-01")
    diff = compare_with(ours_records=[ours("openai", "old", deprecated=True)], live_records=[], all_records=[retired])
    assert diff.deprecated == []


def test_deprecated_entry_carries_the_upstream_date():
    retired = theirs("openai", "old", deprecated=True, deprecation_date="2026-01-01")
    diff = compare_with(ours_records=[ours("openai", "old")], live_records=[], all_records=[retired])
    assert diff.deprecated[0].deprecation_date == "2026-01-01"


def test_repriced_reports_a_rate_that_moved():
    diff = compare_with(
        ours_records=[ours("openai", "m", {"llm_input": "0.002"})],
        live_records=[theirs("openai", "m", {"llm_input": "0.003"})],
    )
    assert diff.repriced == [RateChange("openai", "m", "llm_input", "0.002", "0.003")]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param("0.00250", "0.0025", id="trailing-zero"),
        pytest.param("0.0025", "0.00250000", id="more-trailing-zeros"),
        pytest.param("0", "0.0", id="zero"),
    ],
)
def test_a_rate_that_only_looks_different_does_not_open_a_pr(old, new):
    diff = compare_with(
        ours_records=[ours("openai", "m", {"llm_input": old})],
        live_records=[theirs("openai", "m", {"llm_input": new})],
    )
    assert diff.repriced == []


def test_a_partially_priced_model_is_both_repriced_and_backfilled():
    """Splitting per model rather than per service kind would drop this case entirely."""
    diff = compare_with(
        ours_records=[ours("openai", "m", {"llm_input": "0.002"})],
        live_records=[theirs("openai", "m", {"llm_input": "0.003", "llm_output": "0.012"})],
    )
    assert diff.repriced == [RateChange("openai", "m", "llm_input", "0.002", "0.003")]
    assert diff.backfilled == [RateChange("openai", "m", "llm_output", None, "0.012")]
    assert diff.unpriced == []


def test_unpriced_reports_what_backfill_cannot_close():
    diff = compare_with(
        ours_records=[ours("openai", "m", {"llm_cached_input": "0.0001"})],
        live_records=[theirs("openai", "m")],
    )
    assert diff.unpriced == [PricingGap("openai", "m", ("llm_input", "llm_output"))]


def test_a_model_upstream_cannot_price_at_all_is_unpriced():
    diff = compare_with(ours_records=[ours("openai", "m")], live_records=[])
    assert diff.unpriced == [PricingGap("openai", "m", ("llm_input", "llm_output"))]


def test_pricing_is_compared_even_once_upstream_deprecates_the_model():
    retired = theirs("openai", "old", {"llm_input": "0.003"}, deprecated=True, deprecation_date="2026-01-01")
    diff = compare_with(
        ours_records=[ours("openai", "old", {"llm_input": "0.002"})], live_records=[], all_records=[retired]
    )
    assert diff.repriced == [RateChange("openai", "old", "llm_input", "0.002", "0.003")]


# Layer 6 - dispatch


def test_apply_rate_changes_updates_an_existing_rule():
    diff = Diff(repriced=[RateChange("openai", "gpt-4.1", "llm_input", "0.002", "0.003")])
    updated = apply_rate_changes(json.loads(json.dumps(SEED_ROWS)), diff)
    row = next(r for r in updated if r["model_name"] == "gpt-4.1")
    assert {rule["service_kind"]: rule["unit_price"] for rule in row["rules"]} == {
        "llm_input": "0.003",
        "llm_output": "0.008",
    }


def test_apply_rate_changes_adds_a_missing_rule_without_touching_the_others():
    diff = Diff(backfilled=[RateChange("openai", "gpt-5.4", "llm_output", None, "0.015")])
    updated = apply_rate_changes(json.loads(json.dumps(SEED_ROWS)), diff)
    row = next(r for r in updated if r["model_name"] == "gpt-5.4")
    assert {rule["service_kind"]: rule["unit_price"] for rule in row["rules"]} == {
        "llm_input": "0.0025",
        "llm_output": "0.015",
    }


def test_apply_rate_changes_appends_a_row_for_a_model_the_seed_lacks():
    diff = Diff(backfilled=[RateChange("anthropic", "claude-opus-5", "llm_input", None, "0.005")])
    updated = apply_rate_changes(json.loads(json.dumps(SEED_ROWS)), diff)
    row = next(r for r in updated if r["model_name"] == "claude-opus-5")
    assert row == {
        "provider_type": "anthropic",
        "model_name": "claude-opus-5",
        "rules": [{"service_kind": "llm_input", "unit_price": "0.005"}],
    }


def test_apply_rate_changes_leaves_rows_with_no_catalogue_entry_untouched():
    updated = apply_rate_changes(json.loads(json.dumps(SEED_ROWS)), Diff())
    assert next(r for r in updated if r["model_name"] == "claude-2.1") == SEED_ROWS[2]


def test_migration_numbering_follows_the_highest_existing(repo_root):
    path = generate_migration(repo_root / "apps" / "cost_tracking" / "migrations", TODAY)
    assert path.name == "0010_rate_update_20260915.py"


def test_migration_depends_on_the_previous_one(repo_root):
    path = generate_migration(repo_root / "apps" / "cost_tracking" / "migrations", TODAY)
    assert '("cost_tracking", "0009_add_rules")' in path.read_text()
    assert "load_pricing_data()" in path.read_text()


def test_migration_generation_needs_something_to_depend_on(tmp_path):
    (tmp_path / "migrations").mkdir()
    with pytest.raises(RuntimeError, match="No existing migrations"):
        generate_migration(tmp_path / "migrations", TODAY)


def test_advance_ledger_records_offered_models_as_pending():
    advanced = advance_ledger({}, [theirs("openai", "gpt-9")], TODAY)
    assert advanced[("openai", "gpt-9")] == LedgerEntry("openai", "gpt-9", "2026-09-15", PENDING)


def test_advance_ledger_does_not_overwrite_a_recorded_verdict():
    existing = {("openai", "gpt-9"): LedgerEntry("openai", "gpt-9", "2026-01-01", REJECTED, "audio only")}
    advanced = advance_ledger(existing, [theirs("openai", "gpt-9")], TODAY)
    assert advanced[("openai", "gpt-9")].verdict == REJECTED


def test_advance_ledger_records_the_models_params():
    advanced = advance_ledger({}, [theirs("openai", "gpt-9", params={"reasoning": True})], TODAY)
    assert advanced[("openai", "gpt-9")].params == {"reasoning": True}


def test_advance_ledger_keeps_the_original_first_seen_when_a_model_is_re_offered():
    existing = {("openai", "gpt-9"): LedgerEntry("openai", "gpt-9", "2026-01-01", PENDING)}
    advanced = advance_ledger(existing, [theirs("openai", "gpt-9")], TODAY)
    assert advanced[("openai", "gpt-9")].first_seen == "2026-01-01"


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        pytest.param(Diff(), "false", id="nothing-to-do"),
        pytest.param(Diff(added=[theirs("openai", "m")]), "true", id="a-new-model"),
        pytest.param(Diff(deprecated=[theirs("openai", "m")]), "true", id="only-a-deprecation"),
    ],
)
def test_has_catalogue_work_gates_the_claude_job(diff, expected):
    assert f"has_catalogue_work={expected}" in github_outputs(diff, TODAY, None, None)


def test_a_dry_run_does_not_gate_a_pricing_pr():
    """The gate follows the body file, not the findings: --dry-run finds and writes nothing."""
    diff = Diff(repriced=[RateChange("openai", "m", "llm_input", "0.002", "0.003")])
    assert "has_price_changes=false" in github_outputs(diff, TODAY, None, None)
    assert "pricing_pr_title=" in github_outputs(diff, TODAY, None, None)


def test_gate_variables_name_the_body_files(tmp_path):
    diff = Diff(repriced=[RateChange("openai", "m", "llm_input", "0.002", "0.003")])
    outputs = github_outputs(diff, TODAY, tmp_path / "p.md", tmp_path / "m.md")
    assert "has_price_changes=true" in outputs
    assert f"pricing_pr_body_path={tmp_path / 'p.md'}" in outputs
    assert "has_missing_pricing=true" in outputs


def test_new_model_ids_are_provider_qualified():
    diff = Diff(added=[theirs("azure", "gpt-9"), theirs("openai", "gpt-9")])
    assert "new_model_ids=azure/gpt-9,openai/gpt-9" in github_outputs(diff, TODAY, None, None)


def test_pricing_pr_body_tables_every_change():
    diff = Diff(
        repriced=[RateChange("openai", "m", "llm_input", "0.002", "0.003")],
        backfilled=[RateChange("openai", "m", "llm_output", None, "0.012")],
    )
    body = render_pricing_pr_body(diff)
    assert "| openai | m | llm_input | 0.002 | 0.003 |" in body
    assert "| openai | m | llm_output | 0.012 |" in body


def test_pricing_pr_body_lists_what_it_could_not_cover():
    diff = Diff(
        repriced=[RateChange("openai", "m", "llm_input", "0.002", "0.003")],
        unpriced=[PricingGap("groq", "whisper", ("llm_input", "llm_output"))],
    )
    assert "- `groq/whisper` (missing llm_input, llm_output)" in render_pricing_pr_body(diff)


def test_missing_pricing_issue_body_lists_each_gap():
    body = render_missing_pricing_issue_body([PricingGap("groq", "whisper", ("llm_output",))])
    assert "| groq | whisper | llm_output |" in body


def test_payload_carries_a_ready_to_paste_pricing_entry():
    added = theirs("openai", "gpt-9", {"llm_input": "0.001", "llm_output": "0.004"})
    payload = build_payload(Diff(added=[added]), orphan_rows=[], run_date="2026-09-15T00:00:00Z")
    assert payload["added"][0]["pricing_entry"] == {
        "provider_type": "openai",
        "model_name": "gpt-9",
        "rules": [
            {"service_kind": "llm_input", "unit_price": "0.001"},
            {"service_kind": "llm_output", "unit_price": "0.004"},
        ],
    }


def test_payload_has_no_pricing_entry_for_an_unpriceable_model():
    payload = build_payload(Diff(added=[theirs("openai", "gpt-9")]), orphan_rows=[], run_date="x")
    assert payload["added"][0]["pricing_entry"] is None


def test_payload_carries_the_params_job_two_needs_to_pick_a_class():
    added = theirs("openai", "gpt-9", params={"reasoning": True, "effort_levels": ["low", "xhigh"]})
    payload = build_payload(Diff(added=[added]), orphan_rows=[], run_date="x")
    assert payload["added"][0]["params"] == {"reasoning": True, "effort_levels": ["low", "xhigh"]}


def test_payload_summary_counts_every_section():
    diff = Diff(
        added=[theirs("openai", "a")],
        removed=[ours("openai", "b")],
        repriced=[RateChange("openai", "c", "llm_input", "1", "2")],
    )
    payload = build_payload(diff, orphan_rows=[{}], run_date="x")
    assert payload["summary"] == {
        "added": 1,
        "removed": 1,
        "deprecated": 0,
        "repriced": 1,
        "backfilled": 0,
        "unpriced": 0,
        "seed_rows_without_catalogue_entry": 1,
    }
