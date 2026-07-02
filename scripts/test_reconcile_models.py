"""Unit tests for scripts/reconcile_models.py.

Run with:  pytest scripts/test_reconcile_models.py -v
"""

from __future__ import annotations

import datetime
import json
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest
from reconcile_models import (
    REQUIRED_SERVICE_KINDS,
    MissingPricingEntry,
    RateChange,
    _fmt,
    _format_per_1k,
    _next_migration_number,
    _per_million_to_per_1k,
    _per_token_to_per_1k,
    apply_changes,
    audit_missing_pricing,
    backfill_missing_from_litellm,
    build_pricing_entries,
    compute_changes,
    diffable_models,
    generate_migration,
    load_active_default_models,
    load_priced_models,
    load_registered_models,
    process_candidates,
    rates_from_detail,
    render_missing_pricing_issue_body,
    render_pr_body,
    resolve_pricing_from_litellm,
    resolve_pricing_from_llm_stats,
    seed_index,
)

# Unit-conversion helpers


def test_per_million_to_per_1k():
    """$2.50 per million -> $0.0025 per 1K."""
    assert _per_million_to_per_1k(2.5) == pytest.approx(0.0025)


def test_per_million_to_per_1k_none():
    assert _per_million_to_per_1k(None) is None


def test_per_token_to_per_1k():
    """$0.0000025 per token -> $0.0025 per 1K."""
    assert _per_token_to_per_1k(0.0000025) == pytest.approx(0.0025)


def test_per_token_to_per_1k_none():
    assert _per_token_to_per_1k(None) is None


@pytest.mark.parametrize(
    ("per_million", "expected"),
    [
        pytest.param(2.5, "0.0025", id="dollar-per-million"),
        pytest.param(0.15, "0.00015", id="cent-per-million"),
        pytest.param(0.0, "0", id="zero"),
        pytest.param(0.075, "0.000075", id="sub-cent"),
    ],
)
def test_format_per_1k(per_million, expected):
    assert _format_per_1k(per_million) == expected


# _fmt


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param(0.00250, "0.0025", id="strips_trailing_zeros"),
        pytest.param(0.000075, "0.000075", id="small_value"),
        pytest.param(_per_million_to_per_1k(2.5), "0.0025", id="gpt4o_input_price"),
        pytest.param(_per_million_to_per_1k(10.0), "0.01", id="gpt4o_output_price"),
    ],
)
def test_fmt(value, expected):
    assert _fmt(value) == expected


def test_fmt_decimal_roundtrip():
    """Result of _fmt can be parsed back as a Decimal without float noise."""
    val = _fmt(_per_token_to_per_1k(0.000000075))
    assert val is not None
    assert Decimal(val) > 0


# resolve_pricing_from_llm_stats


def test_resolve_llm_stats_full_pricing():
    details = {
        "input_price": 2.5,
        "output_price": 10.0,
        "cached_input_price": 1.25,
        "cache_write_price": 3.75,
    }
    result = resolve_pricing_from_llm_stats(details)
    assert result is not None
    assert result["llm_input"] == "0.0025"
    assert result["llm_output"] == "0.01"
    assert result["llm_cached_input"] == "0.00125"
    assert result["llm_cache_write"] == "0.00375"


def test_resolve_llm_stats_partial_pricing_no_output():
    """Input present, output missing - still returns a result."""
    result = resolve_pricing_from_llm_stats({"input_price": 1.0})
    assert result is not None
    assert "llm_input" in result
    assert "llm_output" not in result


def test_resolve_llm_stats_unit_conversion():
    """claude-sonnet-4-6: $3/M input, $15/M output."""
    result = resolve_pricing_from_llm_stats({"input_price": 3.0, "output_price": 15.0})
    assert result is not None
    assert result["llm_input"] == "0.003"
    assert result["llm_output"] == "0.015"


@pytest.mark.parametrize(
    "details",
    [
        pytest.param({}, id="empty_payload"),
        pytest.param({"cached_input_price": 0.5}, id="only_cached_no_input_output"),
    ],
)
def test_resolve_llm_stats_returns_none(details):
    """Returns None when both input and output prices are absent."""
    assert resolve_pricing_from_llm_stats(details) is None


def test_rates_from_detail_extracts_known_kinds():
    detail = {
        "input_price": 2.5,
        "output_price": 10.0,
        "cached_input_price": 1.25,
        "cache_write_price": None,
        "unrelated_field": "ignored",
    }
    assert rates_from_detail(detail) == {
        "llm_input": "0.0025",
        "llm_output": "0.01",
        "llm_cached_input": "0.00125",
    }


# resolve_pricing_from_litellm


def test_resolve_litellm_full_pricing():
    litellm_data = {
        "gpt-4o": {
            "input_cost_per_token": 0.0000025,
            "output_cost_per_token": 0.00001,
            "cache_read_input_token_cost": 0.00000125,
        }
    }
    result = resolve_pricing_from_litellm("gpt-4o", litellm_data)
    assert result is not None
    assert result["llm_input"] == "0.0025"
    assert result["llm_output"] == "0.01"
    assert result["llm_cached_input"] == "0.00125"


def test_resolve_litellm_per_token_conversion():
    """$0.000003/token input -> $0.003/1K."""
    litellm_data = {
        "claude-sonnet": {
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        }
    }
    result = resolve_pricing_from_litellm("claude-sonnet", litellm_data)
    assert result is not None
    assert result["llm_input"] == "0.003"
    assert result["llm_output"] == "0.015"


@pytest.mark.parametrize(
    ("model_id", "litellm_data"),
    [
        pytest.param("unknown-model", {}, id="missing_model"),
        pytest.param("gpt-4o", {"gpt-4o": {"context_window": 128000}}, id="no_cost_fields"),
    ],
)
def test_resolve_litellm_returns_none(model_id, litellm_data):
    """Returns None when model is absent or has no pricing fields."""
    assert resolve_pricing_from_litellm(model_id, litellm_data) is None


def test_resolve_litellm_provider_prefix_fallback():
    """When the bare model ID is absent, tries ``provider/model_id``.
    This is the groq/gemma-7b-it case: litellm keys it as ``groq/gemma-7b-it``
    but OCS stores the model name as just ``gemma-7b-it``.
    """
    litellm_data = {
        "groq/gemma-7b-it": {
            "input_cost_per_token": 5e-08,
            "output_cost_per_token": 8e-08,
        }
    }
    result = resolve_pricing_from_litellm("gemma-7b-it", litellm_data, provider="groq")
    assert result is not None
    assert result["llm_input"] == "0.00005"
    assert result["llm_output"] == "0.00008"


def test_resolve_litellm_bare_name_takes_priority_over_prefix():
    """If both bare and prefixed keys exist, bare wins."""
    litellm_data = {
        "some-model": {
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000004,
        },
        "openai/some-model": {
            "input_cost_per_token": 0.000009,
            "output_cost_per_token": 0.000036,
        },
    }
    result = resolve_pricing_from_litellm("some-model", litellm_data, provider="openai")
    assert result is not None
    assert result["llm_input"] == "0.001"  # bare: 0.000001 * 1000


def test_resolve_litellm_no_provider_ignores_prefix():
    """Without a provider, only the bare name is tried."""
    litellm_data = {
        "groq/gemma-7b-it": {
            "input_cost_per_token": 5e-08,
            "output_cost_per_token": 8e-08,
        }
    }
    result = resolve_pricing_from_litellm("gemma-7b-it", litellm_data)
    assert result is None


# build_pricing_entries


def test_build_pricing_entries_single_provider():
    pricing = {"llm_input": "0.0025", "llm_output": "0.01"}
    entries = build_pricing_entries("my-model", ["openai"], pricing)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["provider_type"] == "openai"
    assert entry["model_name"] == "my-model"
    rules_by_kind = {r["service_kind"]: r["unit_price"] for r in entry["rules"]}
    assert rules_by_kind["llm_input"] == "0.0025"
    assert rules_by_kind["llm_output"] == "0.01"


def test_build_pricing_entries_multi_provider():
    pricing = {"llm_input": "0.0025", "llm_output": "0.01"}
    entries = build_pricing_entries("my-model", ["openai", "azure"], pricing)
    assert len(entries) == 2
    assert {e["provider_type"] for e in entries} == {"openai", "azure"}


@pytest.mark.parametrize(
    ("providers", "pricing"),
    [
        pytest.param([], {"llm_input": "0.01"}, id="empty_providers"),
        pytest.param(["openai"], {}, id="empty_pricing"),
    ],
)
def test_build_pricing_entries_empty(providers, pricing):
    """Returns an empty list when there are no providers or no pricing rules."""
    assert build_pricing_entries("x", providers, pricing) == []


# Fixtures for file-system tests

# Single-line and multi-line Model() entries plus 2- and 3-tuple DELETED_MODELS,
# so the ast parser is exercised against all the forms it has to handle.
SAMPLE_DEFAULT_MODELS = textwrap.dedent(
    """\
    DEFAULT_LLM_PROVIDER_MODELS = {
        "openai": [
            Model("gpt-4o", 128000),
            Model("gpt-4o-mini", 128000, is_default=True),
            Model("gpt-4", k(8), deprecated=True),
        ],
        "anthropic": [
            Model("claude-sonnet-4-6", 1000000, is_default=True),
            Model("claude-opus-4-6", k(200)),
            Model(
                "claude-sonnet-4-20250514",
                1000000,
            ),
        ],
        "google": [
            Model("gemini-2.5-flash", 1048576),
        ],
        "google_vertex_ai": [
            Model("gemini-2.5-flash", 1048576),
        ],
    }

    DELETED_MODELS = [
        ("azure", "gpt-4"),
        ("azure", "gpt-35-turbo"),
        ("anthropic", "claude-2.0"),
        ("openai", "gpt-4-turbo", "gpt-4.1"),
    ]
    """
)

SAMPLE_PRICING = [
    {
        "provider_type": "openai",
        "model_name": "gpt-4o",
        "rules": [{"service_kind": "llm_input", "unit_price": "0.0025"}],
    },
    {
        "provider_type": "anthropic",
        "model_name": "claude-sonnet-4-6",
        "rules": [{"service_kind": "llm_input", "unit_price": "0.003"}],
    },
]


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """Minimal repo tree with default_models.py and llm_pricing.json."""
    models_dir = tmp_path / "apps/service_providers/llm_service"
    models_dir.mkdir(parents=True)
    (models_dir / "default_models.py").write_text(SAMPLE_DEFAULT_MODELS)

    pricing_dir = tmp_path / "apps/cost_tracking/seed_data"
    pricing_dir.mkdir(parents=True)
    (pricing_dir / "llm_pricing.json").write_text(json.dumps(SAMPLE_PRICING))

    return tmp_path


# load_registered_models


def test_registered_openai(repo_root):
    registered = load_registered_models(repo_root)
    assert {"gpt-4o", "gpt-4o-mini", "gpt-4"} <= registered["openai"]


def test_registered_anthropic_single_line(repo_root):
    registered = load_registered_models(repo_root)
    assert "claude-sonnet-4-6" in registered["anthropic"]
    assert "claude-opus-4-6" in registered["anthropic"]


def test_registered_anthropic_multi_line_model(repo_root):
    """Multi-line Model( entries (name on next line) are captured correctly."""
    registered = load_registered_models(repo_root)
    assert "claude-sonnet-4-20250514" in registered["anthropic"]


def test_registered_google_vertex(repo_root):
    registered = load_registered_models(repo_root)
    assert "gemini-2.5-flash" in registered["google"]
    assert "gemini-2.5-flash" in registered["google_vertex_ai"]


def test_deleted_models_two_tuple(repo_root):
    """2-tuple DELETED_MODELS entries are captured under their provider."""
    registered = load_registered_models(repo_root)
    assert "gpt-4" in registered.get("azure", set())
    assert "gpt-35-turbo" in registered.get("azure", set())
    assert "claude-2.0" in registered.get("anthropic", set())


def test_deleted_models_three_tuple(repo_root):
    """3-tuple DELETED_MODELS entries (with replacement) are captured."""
    registered = load_registered_models(repo_root)
    assert "gpt-4-turbo" in registered.get("openai", set())


def test_unknown_provider_not_present(repo_root):
    registered = load_registered_models(repo_root)
    assert "deepseek" not in registered


# load_registered_models - awkward formatting robustness
#
# These exercise formatting that a line-by-line regex parser would miss but a
# real Python parser handles: a comment between Model( and the name, a provider
# whose [ sits on the next line, and a DELETED_MODELS tuple split over several
# lines.

SAMPLE_AWKWARD_MODELS = textwrap.dedent(
    """\
    DEFAULT_LLM_PROVIDER_MODELS = {
        "openai": [
            Model(
                # legacy alias kept for back-compat
                "commented-model",
                128000,
            ),
        ],
        "newprov":
            [
                Model("bracket-on-next-line", 1000),
            ],
    }

    DELETED_MODELS = [
        (
            "openai",
            "multiline-deleted",
        ),
        ("anthropic", "claude-x", "claude-y", "extra-elt"),
    ]
    """
)


@pytest.fixture()
def awkward_repo_root(tmp_path: Path) -> Path:
    """Repo tree whose default_models.py uses awkward (but valid) formatting."""
    models_dir = tmp_path / "apps/service_providers/llm_service"
    models_dir.mkdir(parents=True)
    (models_dir / "default_models.py").write_text(SAMPLE_AWKWARD_MODELS)

    pricing_dir = tmp_path / "apps/cost_tracking/seed_data"
    pricing_dir.mkdir(parents=True)
    (pricing_dir / "llm_pricing.json").write_text("[]")
    return tmp_path


def test_registered_comment_between_model_and_name(awkward_repo_root):
    """A comment line between Model( and the name doesn't hide the model."""
    registered = load_registered_models(awkward_repo_root)
    assert "commented-model" in registered["openai"]


def test_registered_provider_bracket_on_next_line(awkward_repo_root):
    """A provider whose opening [ is on the next line is still parsed."""
    registered = load_registered_models(awkward_repo_root)
    assert "bracket-on-next-line" in registered["newprov"]


def test_deleted_models_multiline_tuple(awkward_repo_root):
    """A DELETED_MODELS tuple split across lines is captured."""
    registered = load_registered_models(awkward_repo_root)
    assert "multiline-deleted" in registered.get("openai", set())


def test_deleted_models_n_tuple(awkward_repo_root):
    """A DELETED_MODELS entry with more than 3 elements is captured by (provider, model)."""
    registered = load_registered_models(awkward_repo_root)
    assert "claude-x" in registered.get("anthropic", set())


# load_active_default_models - active vs deleted distinction


def test_load_active_default_models_excludes_deleted(repo_root):
    """DELETED_MODELS are folded into registered (for new-candidate dedup)
    but excluded from active (the missing-pricing audit only flags live
    OCS-managed models)."""
    active = load_active_default_models(repo_root)
    assert ("openai", "gpt-4o") in active
    assert ("anthropic", "claude-sonnet-4-6") in active
    assert ("azure", "gpt-4") not in active
    assert ("anthropic", "claude-2.0") not in active


# load_priced_models


def test_load_priced_models(repo_root):
    priced = load_priced_models(repo_root)
    assert ("openai", "gpt-4o") in priced
    assert ("anthropic", "claude-sonnet-4-6") in priced
    assert ("openai", "claude-sonnet-4-6") not in priced


# process_candidates


def _candidate(model_id, org, context_window=128000, details=None):
    """Build a minimal candidate dict for use in process_candidates tests."""
    return {
        "id": model_id,
        "organization": {"id": org},
        "model_type": "llm",
        "context_window": context_window,
        "details": details
        or {
            "input_price": 2.5,
            "output_price": 10.0,
            "url": f"https://llm-stats.com/models/{model_id}",
            "sources": {},
        },
    }


def test_process_new_model_with_pricing():
    candidates = [_candidate("gpt-new", "openai")]
    registered = {"openai": set(), "azure": set()}
    result = process_candidates(candidates, registered, set(), {})

    assert len(result["new_models"]) == 1
    assert len(result["already_registered"]) == 0
    m = result["new_models"][0]
    assert m["pricing"]["has_pricing"] is True
    assert m["pricing"]["source"] == "llm_stats"
    entry_providers = {e["provider_type"] for e in m["pricing"]["llm_pricing_entries"]}
    assert entry_providers == {"openai", "azure"}


def test_process_fully_registered_model_is_skipped():
    candidates = [_candidate("gpt-4o", "openai")]
    registered = {"openai": {"gpt-4o"}, "azure": {"gpt-4o"}}
    result = process_candidates(candidates, registered, set(), {})
    assert len(result["new_models"]) == 0
    assert len(result["already_registered"]) == 1
    assert result["already_registered"][0]["id"] == "gpt-4o"


def test_process_partially_registered_model_still_processed():
    """Registered in openai but not azure -> still a new_model."""
    candidates = [_candidate("gpt-4o", "openai")]
    registered = {"openai": {"gpt-4o"}, "azure": set()}
    result = process_candidates(candidates, registered, set(), {})
    assert len(result["new_models"]) == 1


def test_process_unpriced_model_flagged():
    candidate = _candidate(
        "mystery-model",
        "deepseek",
        details={"url": "https://llm-stats.com/models/mystery-model", "sources": {}},
    )
    result = process_candidates([candidate], {"deepseek": set()}, set(), {})
    assert len(result["unpriced_models"]) == 1
    assert result["new_models"][0]["pricing"]["has_pricing"] is False


def test_process_litellm_fallback_used_when_llm_stats_missing():
    candidate = _candidate(
        "claude-3",
        "anthropic",
        details={"url": "https://llm-stats.com/models/claude-3", "sources": {}},
    )
    litellm_data = {
        "claude-3": {
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        }
    }
    result = process_candidates([candidate], {"anthropic": set()}, set(), litellm_data)
    m = result["new_models"][0]
    assert m["pricing"]["has_pricing"] is True
    assert m["pricing"]["source"] == "litellm"
    assert m["pricing"]["rates"]["llm_input"] == "0.003"


def test_process_already_priced_providers_excluded():
    candidates = [_candidate("gpt-4o", "openai")]
    registered = {"openai": set(), "azure": set()}
    priced = {("openai", "gpt-4o")}
    result = process_candidates(candidates, registered, priced, {})
    m = result["new_models"][0]
    assert m["already_priced_providers"] == ["openai"]
    entry_providers = {e["provider_type"] for e in m["pricing"]["llm_pricing_entries"]}
    assert entry_providers == {"azure"}


def test_process_pricing_entries_flat_list():
    """anthropic: 1 provider, google: 2 providers -> 3 pricing entries total."""
    candidates = [
        _candidate("model-a", "anthropic"),
        _candidate("model-b", "google"),
    ]
    registered = {"anthropic": set(), "google": set(), "google_vertex_ai": set()}
    result = process_candidates(candidates, registered, set(), {})
    assert len(result["pricing_entries"]) == 3


def test_process_details_error_falls_back_to_litellm():
    candidate = {
        "id": "errored-model",
        "organization": {"id": "openai"},
        "model_type": "llm",
        "context_window": 128000,
        "details_error": "HTTP 404: Not Found",
    }
    litellm_data = {
        "errored-model": {
            "input_cost_per_token": 0.0000025,
            "output_cost_per_token": 0.00001,
        }
    }
    result = process_candidates([candidate], {"openai": set(), "azure": set()}, set(), litellm_data)
    m = result["new_models"][0]
    assert m["pricing"]["has_pricing"] is True
    assert m["pricing"]["source"] == "litellm"


# seed_index + diffable_models


def test_seed_index_keys_by_provider_and_model():
    seed = [
        {
            "provider_type": "openai",
            "model_name": "gpt-4o",
            "rules": [{"service_kind": "llm_input", "unit_price": "0.0025"}],
        },
        {
            "provider_type": "azure",
            "model_name": "gpt-4o",
            "rules": [{"service_kind": "llm_input", "unit_price": "0.0025"}],
        },
    ]
    index = seed_index(seed)
    assert index[("openai", "gpt-4o")] == {"llm_input": "0.0025"}
    assert index[("azure", "gpt-4o")] == {"llm_input": "0.0025"}


def test_diffable_models_skips_non_upstream_providers():
    index = {
        ("openai", "gpt-4o"): {},
        ("azure", "gpt-4o"): {},
        ("groq", "llama-3.3-70b-versatile"): {},
        ("deepseek", "deepseek-chat"): {},
    }
    assert diffable_models(index) == {"gpt-4o"}


# compute_changes


def _detail(rates: dict[str, float]) -> dict:
    """llm-stats detail-shaped dict with `*_price` keys (per million)."""
    keys = {
        "llm_input": "input_price",
        "llm_output": "output_price",
        "llm_cached_input": "cached_input_price",
        "llm_cache_write": "cache_write_price",
    }
    return {**{keys[k]: v for k, v in rates.items()}, "url": "https://llm-stats.com/models/test"}


class TestComputeChanges:
    def test_returns_change_when_rate_differs(self):
        index = {
            ("openai", "gpt-4o"): {"llm_input": "0.0025"},
        }
        fetcher = lambda _m: _detail({"llm_input": 5.0})  # $5/M = $0.005/1K  # noqa: E731

        changes, unmatched = compute_changes(index, fetcher)

        assert unmatched == set()
        assert changes == [
            RateChange(
                provider_type="openai",
                model_name="gpt-4o",
                service_kind="llm_input",
                old_price="0.0025",
                new_price="0.005",
                source_url="https://llm-stats.com/models/test",
            )
        ]

    def test_no_change_when_rate_matches(self):
        index = {("openai", "gpt-4o"): {"llm_input": "0.0025"}}
        fetcher = lambda _m: _detail({"llm_input": 2.5})  # noqa: E731

        changes, unmatched = compute_changes(index, fetcher)

        assert changes == []
        assert unmatched == set()

    def test_records_unmatched_when_fetcher_returns_none(self):
        index = {("openai", "ghost-model"): {"llm_input": "0.0025"}}
        fetcher = lambda _m: None  # noqa: E731

        changes, unmatched = compute_changes(index, fetcher)

        assert changes == []
        assert unmatched == {"ghost-model"}

    def test_change_applied_to_each_diffable_provider(self):
        """One llm-stats rate change applies to every OCS provider wrapping
        that upstream (openai + azure both consume the same gpt-4o pricing)."""
        index = {
            ("openai", "gpt-4o"): {"llm_input": "0.0025"},
            ("azure", "gpt-4o"): {"llm_input": "0.0025"},
        }
        fetcher = lambda _m: _detail({"llm_input": 5.0})  # noqa: E731

        changes, _ = compute_changes(index, fetcher)

        providers = {c.provider_type for c in changes}
        assert providers == {"openai", "azure"}

    def test_skips_non_diffable_provider(self):
        """Groq isn't an llm-stats-tracked upstream - its seed rows aren't
        diffed regardless of what the fetcher would return."""
        index = {("groq", "gemma2-9b-it"): {"llm_input": "0.0002"}}
        fetcher = lambda _m: _detail({"llm_input": 99.0})  # noqa: E731

        changes, unmatched = compute_changes(index, fetcher)

        assert changes == []
        assert unmatched == set()


# apply_changes


class TestApplyChanges:
    def test_replaces_matching_rule(self):
        seed = [
            {
                "provider_type": "openai",
                "model_name": "gpt-4o",
                "rules": [
                    {"service_kind": "llm_input", "unit_price": "0.0025"},
                    {"service_kind": "llm_output", "unit_price": "0.01"},
                ],
            },
        ]
        change = RateChange("openai", "gpt-4o", "llm_input", "0.0025", "0.005", "url")

        updated = apply_changes(seed, [change])

        assert updated[0]["rules"][0] == {"service_kind": "llm_input", "unit_price": "0.005"}
        assert updated[0]["rules"][1] == {"service_kind": "llm_output", "unit_price": "0.01"}

    def test_preserves_unaffected_entries(self):
        seed = [
            {
                "provider_type": "openai",
                "model_name": "gpt-4o",
                "rules": [{"service_kind": "llm_input", "unit_price": "0.0025"}],
            },
            {
                "provider_type": "anthropic",
                "model_name": "claude-haiku",
                "rules": [{"service_kind": "llm_input", "unit_price": "0.001"}],
            },
        ]
        change = RateChange("openai", "gpt-4o", "llm_input", "0.0025", "0.005", "url")

        updated = apply_changes(seed, [change])

        assert updated[1] == seed[1]


# Migration generation


def test_next_migration_number_increments(tmp_path):
    (tmp_path / "0001_initial.py").touch()
    (tmp_path / "0002_seed_pricing.py").touch()
    (tmp_path / "__init__.py").touch()  # should be ignored
    assert _next_migration_number(tmp_path) == 3


def test_generate_migration_writes_file_with_correct_dependency(tmp_path):
    (tmp_path / "0001_initial.py").touch()
    (tmp_path / "0002_seed_pricing.py").touch()

    written = generate_migration(tmp_path, datetime.date(2026, 6, 17))

    assert written.name == "0003_rate_update_20260617.py"
    body = written.read_text()
    assert '("cost_tracking", "0002_seed_pricing")' in body
    assert "load_pricing_data()" in body


# render_pr_body


def test_render_pr_body_includes_table_row_per_change():
    changes = [
        RateChange("openai", "gpt-4o", "llm_input", "0.0025", "0.005", "https://llm-stats.com/models/gpt-4o"),
    ]
    body = render_pr_body(changes, unmatched=set())

    assert "| openai | gpt-4o | llm_input | 0.0025 | 0.005 |" in body
    assert "llm-stats" in body
    assert "## Unmatched models" not in body


def test_render_pr_body_lists_unmatched_when_present():
    body = render_pr_body(changes=[], unmatched={"ghost-model"})

    assert "## Unmatched models" in body
    assert "`ghost-model`" in body


def test_render_pr_body_hyphen_for_missing_old_price():
    change = RateChange("openai", "new-model", "llm_input", None, "0.001", "https://llm-stats.com/models/new-model")
    body = render_pr_body([change], unmatched=set())

    assert "| - | 0.001 |" in body


def test_render_pr_body_includes_backfill_section():
    backfilled = [
        {
            "provider_type": "groq",
            "model_name": "gemma-7b-it",
            "rules": [
                {"service_kind": "llm_input", "unit_price": "0.00005"},
                {"service_kind": "llm_output", "unit_price": "0.00008"},
            ],
        }
    ]
    body = render_pr_body(changes=[], unmatched=set(), backfilled=backfilled)

    assert "## Backfilled from LiteLLM" in body
    assert "gemma-7b-it" in body
    assert "groq" in body


def test_render_pr_body_backfill_only_no_changes_section():
    backfilled = [
        {
            "provider_type": "openai",
            "model_name": "gpt-3.5-turbo",
            "rules": [
                {"service_kind": "llm_input", "unit_price": "0.0005"},
                {"service_kind": "llm_output", "unit_price": "0.0015"},
            ],
        }
    ]
    body = render_pr_body(changes=[], unmatched=set(), backfilled=backfilled)

    # There are no rate changes, so the changes table header should be absent
    assert "Old (per 1K)" not in body
    assert "## Backfilled from LiteLLM" in body


# backfill_missing_from_litellm


class TestBackfillMissingFromLitellm:
    def _entry(self, provider, model, kinds=("llm_input", "llm_output")):
        return MissingPricingEntry(provider, model, tuple(kinds))

    def test_resolves_bare_name_match(self):
        missing = [self._entry("openai", "gpt-3.5-turbo")]
        litellm_data = {
            "gpt-3.5-turbo": {
                "input_cost_per_token": 5e-07,
                "output_cost_per_token": 1.5e-06,
            }
        }
        backfilled, still_missing = backfill_missing_from_litellm(missing, litellm_data)

        assert len(backfilled) == 1
        assert still_missing == []
        entry = backfilled[0]
        assert entry["provider_type"] == "openai"
        assert entry["model_name"] == "gpt-3.5-turbo"
        rules = {r["service_kind"]: r["unit_price"] for r in entry["rules"]}
        assert rules["llm_input"] == "0.0005"
        assert rules["llm_output"] == "0.0015"

    def test_resolves_provider_prefixed_key(self):
        """groq/gemma-7b-it: bare lookup misses, prefixed lookup hits."""
        missing = [self._entry("groq", "gemma-7b-it")]
        litellm_data = {
            "groq/gemma-7b-it": {
                "input_cost_per_token": 5e-08,
                "output_cost_per_token": 8e-08,
            }
        }
        backfilled, still_missing = backfill_missing_from_litellm(missing, litellm_data)

        assert len(backfilled) == 1
        assert still_missing == []

    def test_stays_missing_when_not_in_litellm(self):
        missing = [self._entry("openai", "gpt-5.3")]
        backfilled, still_missing = backfill_missing_from_litellm(missing, {})

        assert backfilled == []
        assert len(still_missing) == 1

    def test_stays_missing_when_litellm_entry_lacks_required_kinds(self):
        """A litellm entry with only cached_input (no input/output) isn't enough."""
        missing = [self._entry("openai", "partial-model")]
        litellm_data = {
            "partial-model": {
                "cache_read_input_token_cost": 0.000001,
            }
        }
        backfilled, still_missing = backfill_missing_from_litellm(missing, litellm_data)

        assert backfilled == []
        assert len(still_missing) == 1

    def test_mixed_resolved_and_unresolved(self):
        missing = [
            self._entry("openai", "gpt-3.5-turbo"),
            self._entry("openai", "gpt-unknown"),
        ]
        litellm_data = {
            "gpt-3.5-turbo": {
                "input_cost_per_token": 5e-07,
                "output_cost_per_token": 1.5e-06,
            }
        }
        backfilled, still_missing = backfill_missing_from_litellm(missing, litellm_data)

        assert len(backfilled) == 1
        assert len(still_missing) == 1
        assert still_missing[0].model_name == "gpt-unknown"

    def test_empty_missing_list(self):
        backfilled, still_missing = backfill_missing_from_litellm([], {})
        assert backfilled == []
        assert still_missing == []


# audit_missing_pricing


def test_audit_empty_when_every_required_kind_priced():
    active = {("openai", "gpt-4o")}
    index = {("openai", "gpt-4o"): {"llm_input": "0.0025", "llm_output": "0.01"}}
    assert audit_missing_pricing(active, index) == []


def test_audit_flags_both_kinds_missing_when_seed_has_no_entry():
    active = {("openai", "gpt-mystery")}
    assert audit_missing_pricing(active, {}) == [
        MissingPricingEntry("openai", "gpt-mystery", ("llm_input", "llm_output"))
    ]


def test_audit_flags_only_kind_actually_missing():
    """A seed entry with llm_input but not llm_output flags only llm_output."""
    active = {("openai", "gpt-half")}
    index = {("openai", "gpt-half"): {"llm_input": "0.001"}}
    assert audit_missing_pricing(active, index) == [MissingPricingEntry("openai", "gpt-half", ("llm_output",))]


def test_audit_ignores_models_outside_active():
    """A seed entry for an inactive (e.g. deleted) model doesn't suppress
    the flag for an active model with no entry, and the inactive model is
    not itself audited."""
    active = {("openai", "active-model")}
    index = {("openai", "deleted-model"): {"llm_input": "0.001", "llm_output": "0.002"}}
    result = audit_missing_pricing(active, index)
    assert result == [MissingPricingEntry("openai", "active-model", ("llm_input", "llm_output"))]


def test_audit_required_kinds_are_input_and_output_only():
    """A cached-input-only entry doesn't satisfy the audit."""
    active = {("openai", "cache-only")}
    index = {("openai", "cache-only"): {"llm_cached_input": "0.0001"}}
    result = audit_missing_pricing(active, index)
    assert result == [MissingPricingEntry("openai", "cache-only", ("llm_input", "llm_output"))]
    assert "llm_cached_input" not in REQUIRED_SERVICE_KINDS


def test_audit_results_sorted_by_provider_and_model():
    active = {
        ("openai", "z-model"),
        ("anthropic", "a-model"),
        ("openai", "a-model"),
    }
    result = audit_missing_pricing(active, {})
    keys = [(e.provider_type, e.model_name) for e in result]
    assert keys == sorted(keys)


# render_missing_pricing_issue_body


def test_render_missing_pricing_issue_body_one_row_per_entry():
    entries = [
        MissingPricingEntry("openai", "gpt-mystery", ("llm_input", "llm_output")),
        MissingPricingEntry("anthropic", "claude-mystery", ("llm_output",)),
    ]
    body = render_missing_pricing_issue_body(entries)

    assert "| openai | gpt-mystery | llm_input, llm_output |" in body
    assert "| anthropic | claude-mystery | llm_output |" in body
    assert "backfill_pricing_seed" in body
