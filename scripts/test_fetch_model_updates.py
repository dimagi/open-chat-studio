"""
Unit tests for scripts/fetch_model_updates.py.

Run with:  pytest scripts/test_fetch_model_updates.py -v
"""

from __future__ import annotations

import json
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest
from fetch_model_updates import (
    _fmt,
    _per_million_to_per_1k,
    _per_token_to_per_1k,
    build_pricing_entries,
    load_priced_models,
    load_registered_models,
    process_candidates,
    resolve_pricing_from_litellm,
    resolve_pricing_from_llm_stats,
)

# ---------------------------------------------------------------------------
# Unit-conversion helpers
# ---------------------------------------------------------------------------


def test_per_million_to_per_1k():
    """$2.50 per million → $0.0025 per 1K."""
    assert _per_million_to_per_1k(2.5) == pytest.approx(0.0025)


def test_per_million_to_per_1k_none():
    assert _per_million_to_per_1k(None) is None


def test_per_token_to_per_1k():
    """$0.0000025 per token → $0.0025 per 1K."""
    assert _per_token_to_per_1k(0.0000025) == pytest.approx(0.0025)


def test_per_token_to_per_1k_none():
    assert _per_token_to_per_1k(None) is None


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------


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
    """_fmt converts per-1K token prices to compact decimal strings."""
    assert _fmt(value) == expected


def test_fmt_decimal_roundtrip():
    """Result of _fmt can be parsed back as a Decimal without float noise."""
    val = _fmt(_per_token_to_per_1k(0.000000075))
    assert val is not None
    assert Decimal(val) > 0


# ---------------------------------------------------------------------------
# resolve_pricing_from_llm_stats
# ---------------------------------------------------------------------------


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
    """input present, output missing → still returns a result."""
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


# ---------------------------------------------------------------------------
# resolve_pricing_from_litellm
# ---------------------------------------------------------------------------


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
    """$0.000003/token input → $0.003/1K."""
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


# ---------------------------------------------------------------------------
# build_pricing_entries
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fixtures for file-system tests
# ---------------------------------------------------------------------------

# Includes both single-line Model() entries and a multi-line form, plus 2- and
# 3-tuple DELETED_MODELS entries — all of which the ast parser must handle.
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


# ---------------------------------------------------------------------------
# load_registered_models
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# load_registered_models — awkward formatting robustness
#
# These exercise formatting that a line-by-line regex parser misses but a real
# Python parser handles: a comment between ``Model(`` and the name, a provider
# whose ``[`` sits on the next line, and a DELETED_MODELS tuple split over
# several lines.
# ---------------------------------------------------------------------------

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
    """A comment line between ``Model(`` and the name doesn't hide the model."""
    registered = load_registered_models(awkward_repo_root)
    assert "commented-model" in registered["openai"]


def test_registered_provider_bracket_on_next_line(awkward_repo_root):
    """A provider whose opening ``[`` is on the next line is still parsed."""
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


# ---------------------------------------------------------------------------
# load_priced_models
# ---------------------------------------------------------------------------


def test_load_priced_models(repo_root):
    priced = load_priced_models(repo_root)
    assert ("openai", "gpt-4o") in priced
    assert ("anthropic", "claude-sonnet-4-6") in priced
    assert ("openai", "claude-sonnet-4-6") not in priced


# ---------------------------------------------------------------------------
# process_candidates
# ---------------------------------------------------------------------------


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
    """Registered in openai but not azure → still a new_model."""
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
    """anthropic: 1 provider, google: 2 providers → 3 pricing entries total."""
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
