"""Unit tests for scripts/reconcile_models.py.

Run with:  pytest scripts/test_reconcile_models.py -v
"""

from __future__ import annotations

import datetime
import email.message
import io
import json
import textwrap
import urllib.error
import urllib.parse
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import reconcile_models
from reconcile_models import (
    DEFAULT_RETRY_AFTER_SECONDS,
    LITELLM_SOURCE_URL,
    MAX_NEW_MODELS_PER_RUN,
    MAX_RETRY_AFTER_SECONDS,
    MAX_TOTAL_BURST_WAIT_SECONDS,
    RATE_LIMIT_JITTER_SECONDS,
    REQUIRED_SERVICE_KINDS,
    Candidate,
    MissingPricingEntry,
    RateChange,
    _commit_price_changes,
    _fmt,
    _get_json,
    _next_migration_number,
    _per_token_to_per_1k,
    _ReconcileResults,
    apply_changes,
    audit_deprecated_upstream,
    audit_missing_pricing,
    backfill_missing_from_litellm,
    build_pricing_entries,
    compute_changes,
    diffable_models,
    eligible_models,
    fetch_baseline,
    generate_migration,
    load_active_default_models,
    load_priced_models,
    load_registered_models,
    load_seed,
    process_candidates,
    render_missing_pricing_issue_body,
    render_pr_body,
    resolve_pricing,
    resolve_pricing_from_litellm,
    seed_index,
    select_candidates,
)

# Unit-conversion helpers


def test_per_token_to_per_1k():
    """$0.0000025 per token -> $0.0025 per 1K."""
    assert _per_token_to_per_1k(0.0000025) == pytest.approx(0.0025)


def test_per_token_to_per_1k_none():
    assert _per_token_to_per_1k(None) is None


# _fmt


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param(0.00250, "0.0025", id="strips_trailing_zeros"),
        pytest.param(0.000075, "0.000075", id="small_value"),
        pytest.param(_per_token_to_per_1k(0.0000025), "0.0025", id="gpt4o_input_price"),
        pytest.param(_per_token_to_per_1k(0.00001), "0.01", id="gpt4o_output_price"),
    ],
)
def test_fmt(value, expected):
    assert _fmt(value) == expected


def test_fmt_decimal_roundtrip():
    """Result of _fmt can be parsed back as a Decimal without float noise."""
    val = _fmt(_per_token_to_per_1k(0.000000075))
    assert val is not None
    assert Decimal(val) > 0


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


def test_resolve_litellm_provider_prefix_takes_priority_over_bare_name():
    """If both bare and prefixed keys exist, the provider-prefixed key wins."""
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
    assert result["llm_input"] == "0.009"  # openai/some-model: 0.000009 * 1000


def test_resolve_litellm_azure_rate_not_shadowed_by_bare_openai_key():
    """Azure resells OpenAI models at its own rates, and LiteLLM's bare key
    holds OpenAI's. Reading the bare key for an azure lookup bills Azure usage
    at OpenAI's price.
    """
    litellm_data = {
        "gpt-4o-mini": {
            "input_cost_per_token": 1.5e-07,
            "output_cost_per_token": 6e-07,
        },
        "azure/gpt-4o-mini": {
            "input_cost_per_token": 1.65e-07,
            "output_cost_per_token": 6.6e-07,
        },
    }
    result = resolve_pricing_from_litellm("gpt-4o-mini", litellm_data, provider="azure")
    assert result is not None
    assert result["llm_input"] == "0.000165"
    assert result["llm_output"] == "0.00066"


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


def _litellm(key, tokens=128000, **extra):
    """A LiteLLM price-table entry - the only upstream source now."""
    return {
        key: {
            "litellm_provider": extra.pop("litellm_provider", "openai"),
            "mode": extra.pop("mode", "chat"),
            "input_cost_per_token": 0.0000025,
            "output_cost_per_token": 0.00001,
            "max_input_tokens": tokens,
            **extra,
        }
    }


def _candidate(model_id, providers=("openai", "azure"), keys=None, recent=True):
    """A merged record as `eligible_models` produces it."""
    return {
        "id": model_id,
        "providers": list(providers),
        "keys": keys or {p: model_id for p in providers},
        "deprecation_date": None,
        "recently_published": recent,
    }


def test_process_new_model_with_pricing():
    candidates = [_candidate("gpt-new")]
    registered = {"openai": set(), "azure": set()}
    result = process_candidates(candidates, registered, set(), _litellm("gpt-new"))

    assert len(result["new_models"]) == 1
    assert len(result["already_registered"]) == 0
    m = result["new_models"][0]
    assert m["pricing"]["has_pricing"] is True
    assert m["pricing"]["source"] == "litellm"
    assert m["token_limit"] == 128000
    entry_providers = {e["provider_type"] for e in m["pricing"]["llm_pricing_entries"]}
    assert entry_providers == {"openai", "azure"}


def test_process_fully_registered_model_is_skipped():
    candidates = [_candidate("gpt-4o")]
    registered = {"openai": {"gpt-4o"}, "azure": {"gpt-4o"}}
    result = process_candidates(candidates, registered, set(), {})
    assert len(result["new_models"]) == 0
    assert len(result["already_registered"]) == 1
    assert result["already_registered"][0]["id"] == "gpt-4o"


def test_process_partially_registered_model_still_processed():
    """Registered in openai but not azure -> still a new_model."""
    candidates = [_candidate("gpt-4o")]
    registered = {"openai": {"gpt-4o"}, "azure": set()}
    result = process_candidates(candidates, registered, set(), {})
    assert len(result["new_models"]) == 1


def test_process_unpriced_model_flagged():
    candidate = _candidate("mystery-model", providers=("deepseek",))
    result = process_candidates([candidate], {"deepseek": set()}, set(), {})
    assert len(result["unpriced_models"]) == 1
    assert result["new_models"][0]["pricing"]["has_pricing"] is False


def test_process_already_priced_providers_excluded():
    candidates = [_candidate("gpt-4o")]
    registered = {"openai": set(), "azure": set()}
    priced = {("openai", "gpt-4o")}
    result = process_candidates(candidates, registered, priced, _litellm("gpt-4o"))
    m = result["new_models"][0]
    assert m["already_priced_providers"] == ["openai"]
    entry_providers = {e["provider_type"] for e in m["pricing"]["llm_pricing_entries"]}
    assert entry_providers == {"azure"}


def test_process_pricing_entries_flat_list():
    """anthropic: 1 provider, google: 2 providers -> 3 pricing entries total."""
    candidates = [
        _candidate("model-a", providers=("anthropic",)),
        _candidate("model-b", providers=("google", "google_vertex_ai")),
    ]
    registered = {"anthropic": set(), "google": set(), "google_vertex_ai": set()}
    litellm_data = {**_litellm("model-a"), **_litellm("model-b")}
    result = process_candidates(candidates, registered, set(), litellm_data)
    assert len(result["pricing_entries"]) == 3


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
        ("deepseek", "deepseek-v4-flash"): {},
    }
    assert diffable_models(index) == {"gpt-4o"}


# compute_changes


def _price_table(model: str, **per_1k: float) -> dict:
    """A LiteLLM price-table entry, expressed in the per-1K rates we compare."""
    keys = {
        "llm_input": "input_cost_per_token",
        "llm_output": "output_cost_per_token",
        "llm_cached_input": "cache_read_input_token_cost",
    }
    return {model: {keys[k]: v / 1000 for k, v in per_1k.items()}}


class TestComputeChanges:
    def test_returns_change_when_rate_differs(self):
        index = {("openai", "gpt-4o"): {"llm_input": "0.0025"}}

        changes, unmatched = compute_changes(index, _price_table("gpt-4o", llm_input=0.005))

        assert unmatched == set()
        assert changes == [
            RateChange(
                provider_type="openai",
                model_name="gpt-4o",
                service_kind="llm_input",
                old_price="0.0025",
                new_price="0.005",
                source_url=LITELLM_SOURCE_URL,
            )
        ]

    def test_no_change_when_rate_matches(self):
        index = {("openai", "gpt-4o"): {"llm_input": "0.0025"}}

        changes, unmatched = compute_changes(index, _price_table("gpt-4o", llm_input=0.0025))

        assert changes == []
        assert unmatched == set()

    def test_records_unmatched_when_litellm_has_no_entry(self):
        index = {("openai", "ghost-model"): {"llm_input": "0.0025"}}

        changes, unmatched = compute_changes(index, {})

        assert changes == []
        assert unmatched == {"ghost-model"}

    def test_change_applied_to_each_diffable_provider(self):
        """One upstream rate change applies to every OCS provider wrapping it
        (openai + azure both consume the same gpt-4o pricing)."""
        index = {
            ("openai", "gpt-4o"): {"llm_input": "0.0025"},
            ("azure", "gpt-4o"): {"llm_input": "0.0025"},
        }

        changes, _ = compute_changes(index, _price_table("gpt-4o", llm_input=0.005))

        providers = {c.provider_type for c in changes}
        assert providers == {"openai", "azure"}

    def test_makes_no_network_calls(self, urlopen):
        """The diff reads the price table already in memory. This is the whole
        point of the rework - it used to be one request per seed model."""
        _, requests = urlopen()
        index = {("openai", "gpt-4o"): {"llm_input": "0.0025"}}

        compute_changes(index, _price_table("gpt-4o", llm_input=0.005))

        assert requests == []

    def test_skips_non_diffable_provider(self):
        """Groq seed rows aren't under automated rewrite, whatever LiteLLM
        says about them."""
        index = {("groq", "gemma2-9b-it"): {"llm_input": "0.0002"}}

        changes, unmatched = compute_changes(index, _price_table("gemma2-9b-it", llm_input=99.0))

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
    # Each rule gets its own row with 4 cells.
    assert "| groq | gemma-7b-it | llm_input | 0.00005 |" in body
    assert "| groq | gemma-7b-it | llm_output | 0.00008 |" in body


def test_commit_price_changes_merges_backfill_into_partial_entry(repo_root, tmp_path):
    """A backfilled entry whose (provider, model) already exists in the seed
    (e.g. with only llm_cached_input) should have its rules merged in rather
    than silently dropped.
    """
    # Seed the file with a partial entry (cached_input only, no input/output).
    seed_path = repo_root / "apps/cost_tracking/seed_data/llm_pricing.json"
    partial_seed = [
        {
            "provider_type": "openai",
            "model_name": "gpt-partial",
            "rules": [{"service_kind": "llm_cached_input", "unit_price": "0.00025"}],
        }
    ]
    seed_path.write_text(json.dumps(partial_seed))

    # Ensure migrations dir exists.
    migrations_dir = repo_root / "apps/cost_tracking/migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_initial.py").touch()

    backfilled = [
        {
            "provider_type": "openai",
            "model_name": "gpt-partial",
            "rules": [
                {"service_kind": "llm_input", "unit_price": "0.0005"},
                {"service_kind": "llm_output", "unit_price": "0.0015"},
            ],
        }
    ]
    results = _ReconcileResults(
        candidates=[],
        backlog=[],
        deprecated_upstream=[],
        classification={"new_models": [], "already_registered": [], "unpriced_models": [], "pricing_entries": []},
        changes=[],
        unmatched_diff=set(),
        missing=[],
        backfilled=backfilled,
    )

    _commit_price_changes(results, repo_root, tmp_path / "out.json", datetime.date(2026, 7, 1))

    written = load_seed(seed_path)
    assert len(written) == 1
    rules_by_kind = {r["service_kind"]: r["unit_price"] for r in written[0]["rules"]}
    # All three kinds should be present after the merge.
    assert rules_by_kind["llm_cached_input"] == "0.00025"  # preserved
    assert rules_by_kind["llm_input"] == "0.0005"  # backfilled
    assert rules_by_kind["llm_output"] == "0.0015"  # backfilled


def test_commit_price_changes_backfill_does_not_overwrite_curated_prices(repo_root, tmp_path):
    """When the seed already has a curated price for a kind that LiteLLM also
    returns, the curated value must be preserved — backfill only fills *gaps*.
    """
    # Seed has a curated llm_input; llm_output is missing.
    seed_path = repo_root / "apps/cost_tracking/seed_data/llm_pricing.json"
    curated_input = "0.00999"  # deliberately different from LiteLLM's value
    partial_seed = [
        {
            "provider_type": "openai",
            "model_name": "gpt-curated",
            "rules": [{"service_kind": "llm_input", "unit_price": curated_input}],
        }
    ]
    seed_path.write_text(json.dumps(partial_seed))

    migrations_dir = repo_root / "apps/cost_tracking/migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_initial.py").touch()

    # LiteLLM backfill returns BOTH input and output — input should not clobber
    # the curated value.
    backfilled = [
        {
            "provider_type": "openai",
            "model_name": "gpt-curated",
            "rules": [
                {"service_kind": "llm_input", "unit_price": "0.00001"},  # different
                {"service_kind": "llm_output", "unit_price": "0.00004"},
            ],
        }
    ]
    results = _ReconcileResults(
        candidates=[],
        backlog=[],
        deprecated_upstream=[],
        classification={"new_models": [], "already_registered": [], "unpriced_models": [], "pricing_entries": []},
        changes=[],
        unmatched_diff=set(),
        missing=[],
        backfilled=backfilled,
    )
    _commit_price_changes(results, repo_root, tmp_path / "out.json", datetime.date(2026, 7, 1))

    written = load_seed(seed_path)
    rules_by_kind = {r["service_kind"]: r["unit_price"] for r in written[0]["rules"]}
    assert rules_by_kind["llm_input"] == curated_input, "curated price must not be overwritten"
    assert rules_by_kind["llm_output"] == "0.00004", "missing kind should be filled"


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


class TestBackfillMissingFromLitellm:  # noqa: D101
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

    @pytest.mark.parametrize(
        ("model_id", "litellm_data"),
        [
            pytest.param("gpt-5.3", {}, id="not_in_litellm"),
            pytest.param(
                "partial-model",
                {"partial-model": {"cache_read_input_token_cost": 0.000001}},
                id="cached_input_only_not_enough",
            ),
            pytest.param(
                "groq-model",
                {},  # neither bare nor prefixed key exists
                id="provider_prefix_also_missing",
            ),
        ],
    )
    def test_stays_missing(self, model_id, litellm_data):
        """Entry remains in still_missing when litellm can't fully resolve it."""
        missing = [self._entry("openai", model_id)]
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


# resolve_pricing — provider forwarding to litellm


def test_resolve_pricing_uses_the_key_discovery_matched():
    """A candidate carries the exact price-table key it was found under, so a
    namespaced entry like `groq/gemma-7b-it` is read directly."""
    candidate = Candidate(
        {"id": "gemma-7b-it", "providers": ["groq"], "keys": {"groq": "groq/gemma-7b-it"}},
    )
    litellm_data = {"groq/gemma-7b-it": {"input_cost_per_token": 5e-08, "output_cost_per_token": 8e-08}}

    result = resolve_pricing(candidate, litellm_data)

    assert result.source == "litellm"
    assert result.rates == {"llm_input": "0.00005", "llm_output": "0.00008"}


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


# Upstream request budget
#
# Every HTTP request goes through `_get_json`, so patching it gives an exact
# per-run call count. There is one upstream now - LiteLLM's price table - so a
# run costs the current file, the baseline file, and the commit lookup that
# finds the baseline. No credentials, no metered API, no per-model requests.


def _entry(key, provider="openai", mode="chat", **extra):
    return {key: {"litellm_provider": provider, "mode": mode, "max_input_tokens": 128000, **extra}}


class _CallRecorder:
    """Stand-in for `reconcile_models._get_json` that records every URL."""

    def __init__(self, current: dict | None = None, baseline: dict | None = None, sha: str = "abc123"):
        self.urls: list[str] = []
        self._current = current or {}
        self._baseline = baseline if baseline is not None else {}
        self._sha = sha

    def __call__(self, url: str, headers: dict[str, str] | None = None):
        self.urls.append(url)
        if url.startswith("https://api.github.com/"):
            return [{"sha": self._sha}] if self._sha else []
        if self._sha and self._sha in url:
            return dict(self._baseline)
        return dict(self._current)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "github_api": sum(1 for u in self.urls if u.startswith("https://api.github.com/")),
            "raw_files": sum(1 for u in self.urls if u.startswith("https://raw.githubusercontent.com/")),
            "total": len(self.urls),
        }


@pytest.fixture()
def recorder(monkeypatch):
    def _make(**kwargs):
        rec = _CallRecorder(**kwargs)
        monkeypatch.setattr(reconcile_models, "_get_json", rec)
        return rec

    return _make


def test_call_budget_formula(recorder, repo_root):
    """One run = 1 commit lookup + the current file + the baseline file."""
    rec = recorder(current=_entry("gpt-new"), baseline={})

    reconcile_models._run_reconciliation(repo_root)

    assert rec.counts == {"github_api": 1, "raw_files": 2, "total": 3}


def test_call_budget_is_independent_of_seed_size(recorder, repo_root):
    """The old diff cost one request per diffable seed model. Growing the seed
    must cost nothing."""
    (repo_root / reconcile_models.LLM_PRICING_REL_PATH).write_text(
        json.dumps(
            [
                {
                    "provider_type": "openai",
                    "model_name": f"gpt-{i}",
                    "rules": [{"service_kind": "llm_input", "unit_price": "0.001"}],
                }
                for i in range(200)
            ]
        )
    )
    rec = recorder(current=_entry("gpt-4o"))

    reconcile_models._run_reconciliation(repo_root)

    assert rec.counts["total"] == 3


def test_run_survives_an_unreachable_commit_history(recorder, repo_root, capsys):
    """Ordering is a nicety. If GitHub can't be reached the run continues with
    everything treated as equally recent."""
    rec = recorder(current=_entry("gpt-new"), sha="")

    results = reconcile_models._run_reconciliation(repo_root)

    assert [c["id"] for c in results.candidates] == ["gpt-new"]
    assert rec.counts["github_api"] == 1


def test_no_credentialed_requests(recorder, repo_root):
    """The bearer token is gone; nothing should reach a metered API."""
    rec = recorder(current=_entry("gpt-new"))

    reconcile_models._run_reconciliation(repo_root)

    assert all(u.startswith(("https://raw.githubusercontent.com/", "https://api.github.com/")) for u in rec.urls)


# eligible_models


def test_eligible_models_merges_providers_for_one_model():
    """`gpt-4o` on openai and azure is one candidate offered to two providers,
    read from the data rather than a hardcoded org mapping."""
    data = {**_entry("gpt-4o"), **_entry("azure/gpt-4o", provider="azure")}

    merged = eligible_models(data)

    assert merged["gpt-4o"]["providers"] == ["azure", "openai"]
    assert merged["gpt-4o"]["keys"] == {"openai": "gpt-4o", "azure": "azure/gpt-4o"}


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(_entry("dall-e-3", mode="image_generation"), "image", id="image_generation"),
        pytest.param(_entry("ada-002", mode="embedding"), "embedding", id="embedding"),
        pytest.param(_entry("whisper", mode="audio_transcription"), "audio", id="audio"),
        pytest.param(_entry("mistral-large", provider="mistral"), "provider", id="unsupported_provider"),
    ],
)
def test_eligible_models_excludes(data, reason):
    assert eligible_models(data) == {}


def test_eligible_models_excludes_audio_only_output():
    """Google's lyria music models are tagged mode `chat` but only emit audio."""
    data = _entry("lyria-3.5", provider="gemini", supported_output_modalities=["audio"])
    assert eligible_models(data) == {}


def test_eligible_models_keeps_text_output():
    data = _entry("gemini-3-pro", provider="gemini", supported_output_modalities=["text", "audio"])
    assert "gemini-3-pro" in eligible_models(data)


def test_eligible_models_keeps_the_responses_mode():
    """Four models OCS already registers are listed with mode `responses`."""
    assert "gpt-5-pro" in eligible_models(_entry("gpt-5-pro", mode="responses"))


def test_eligible_models_excludes_already_deprecated():
    """No point proposing a model upstream has already retired."""
    data = _entry("gpt-old", deprecation_date="2020-01-01")
    assert eligible_models(data, today=datetime.date(2026, 9, 10)) == {}


def test_eligible_models_keeps_a_future_deprecation():
    data = _entry("gpt-soon", deprecation_date="2027-01-01")
    assert "gpt-soon" in eligible_models(data, today=datetime.date(2026, 9, 10))


def test_eligible_models_strips_region_prefixes():
    assert "gpt-6-astra" in eligible_models(_entry("azure/us/gpt-6-astra", provider="azure"))


# select_candidates


def test_select_candidates_is_state_based_not_time_windowed():
    """A model that predates the baseline but was never registered is still a
    candidate. This is what heals a day the workflow was broken."""
    data = _entry("long-standing-model")

    candidates, backlog = select_candidates(data, baseline=data, registered={})

    assert [c["id"] for c in candidates] == ["long-standing-model"]
    assert candidates[0]["recently_published"] is False
    assert backlog == []


def test_select_candidates_offers_newly_published_first():
    data = {**_entry("old-model"), **_entry("brand-new")}

    candidates, _ = select_candidates(data, baseline=_entry("old-model"), registered={})

    assert [c["id"] for c in candidates] == ["brand-new", "old-model"]


def test_select_candidates_skips_fully_registered():
    data = {**_entry("gpt-4o"), **_entry("gpt-new")}
    registered = {"openai": {"gpt-4o"}}

    candidates, _ = select_candidates(data, baseline={}, registered=registered)

    assert [c["id"] for c in candidates] == ["gpt-new"]


def test_select_candidates_keeps_a_partially_registered_model():
    """Registered on openai but not azure - still worth offering for azure."""
    data = {**_entry("gpt-4o"), **_entry("azure/gpt-4o", provider="azure")}

    candidates, _ = select_candidates(data, baseline={}, registered={"openai": {"gpt-4o"}})

    assert [c["id"] for c in candidates] == ["gpt-4o"]


def test_select_candidates_caps_the_run_and_backlogs_the_rest():
    data = {}
    for i in range(MAX_NEW_MODELS_PER_RUN + 15):
        data.update(_entry(f"gpt-{i:03d}"))

    candidates, backlog = select_candidates(data, baseline={}, registered={})

    assert len(candidates) == MAX_NEW_MODELS_PER_RUN
    assert len(backlog) == 15


# audit_deprecated_upstream


def test_audit_deprecated_flags_active_models_past_their_date():
    active = {("openai", "gpt-old"), ("openai", "gpt-current")}
    data = {**_entry("gpt-old", deprecation_date="2026-02-17"), **_entry("gpt-current")}

    result = audit_deprecated_upstream(active, data, today=datetime.date(2026, 9, 10))

    assert result == [{"provider_type": "openai", "model_name": "gpt-old", "deprecation_date": "2026-02-17"}]


def test_audit_deprecated_ignores_future_dates():
    active = {("openai", "gpt-soon")}
    data = _entry("gpt-soon", deprecation_date="2027-01-01")

    assert audit_deprecated_upstream(active, data, today=datetime.date(2026, 9, 10)) == []


def test_audit_deprecated_tolerates_a_malformed_date():
    active = {("openai", "gpt-odd")}
    data = _entry("gpt-odd", deprecation_date="soon-ish")

    assert audit_deprecated_upstream(active, data, today=datetime.date(2026, 9, 10)) == []


# fetch_baseline


def test_fetch_baseline_asks_for_the_commit_before_the_cutoff(recorder):
    rec = recorder(baseline=_entry("old-model"))

    fetch_baseline(7, today=datetime.date(2026, 9, 10))

    assert "until=2026-09-03T00:00:00Z" in rec.urls[0]


def test_fetch_baseline_is_empty_when_history_is_unavailable(recorder):
    recorder(sha="")
    assert fetch_baseline(7, today=datetime.date(2026, 9, 10)) == {}


# 429 handling
#
# https://docs.llm-stats.com/api-reference/rate-limits-and-headers
# Retry-After is in seconds and present only on a 429. `error.limit_type`
# says which limit was hit: `rate_limit_exceeded` is the rolling-60s burst
# window and clears on its own; `quota_exceeded` is the UTC-day quota and
# does not.


RAW_URL = "https://raw.githubusercontent.com/BerriAI/litellm/refs/heads/main/x.json"


def _http_error(status: int, retry_after: str | None = None, limit_type: str | None = None) -> urllib.error.HTTPError:
    body = json.dumps({"error": {"limit_type": limit_type}} if limit_type else {}).encode()
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(RAW_URL, status, "Too Many Requests", headers, io.BytesIO(body))


@pytest.fixture()
def urlopen(monkeypatch):
    """Queue responses for `_get_json`. Returns a `(sleeps, requests)` pair of
    the durations slept and the `urllib.request.Request` objects sent."""
    sleeps: list[float] = []
    requests: list[Any] = []

    def _install(*responses):
        queue = list(responses)

        def _fake_urlopen(req, timeout=None):
            requests.append(req)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return io.BytesIO(json.dumps(item).encode())

        monkeypatch.setattr(reconcile_models.urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.setattr(reconcile_models.time, "sleep", sleeps.append)
        monkeypatch.setattr(reconcile_models.random, "uniform", lambda _a, _b: 0.0)
        return sleeps, requests

    return _install


def test_burst_429_is_retried_after_retry_after_seconds(urlopen):
    sleeps, _ = urlopen(_http_error(429, "7", "rate_limit_exceeded"), {"ok": True})

    assert _get_json(RAW_URL) == {"ok": True}
    assert sleeps == [7.0]


def test_burst_429_sleep_gets_jitter(monkeypatch, urlopen):
    sleeps, _ = urlopen(_http_error(429, "7", "rate_limit_exceeded"), {"ok": True})
    monkeypatch.setattr(reconcile_models.random, "uniform", lambda _a, b: b)

    _get_json(RAW_URL)

    assert sleeps == [7.0 + RATE_LIMIT_JITTER_SECONDS]


def test_backoff_window_doubles_per_consecutive_miss(urlopen, monkeypatch):
    """An unparseable Retry-After is treated as absent, and the window the
    jitter is drawn from doubles so a bad header can't spin a tight loop."""
    sleeps, _ = urlopen(
        _http_error(429, None),
        _http_error(429, "not-a-number"),
        {"ok": True},
    )
    monkeypatch.setattr(reconcile_models.random, "uniform", lambda _a, b: b)

    assert _get_json(RAW_URL) == {"ok": True}
    assert sleeps == [DEFAULT_RETRY_AFTER_SECONDS, DEFAULT_RETRY_AFTER_SECONDS * 2]


def test_retry_after_is_capped(urlopen):
    """Burst clears within 60s, so an hour-long Retry-After would outlive the
    CI job for no benefit."""
    sleeps, _ = urlopen(_http_error(429, "3600", "rate_limit_exceeded"), {"ok": True})

    _get_json(RAW_URL)

    assert sleeps == [MAX_RETRY_AFTER_SECONDS]


def test_burst_429_is_retried_for_as_long_as_it_takes(urlopen):
    """Burst is a rolling 60s window, so it always clears. A run sleeps
    through it rather than failing - there is no retry ceiling."""
    sleeps, _ = urlopen(*[_http_error(429, "5", "rate_limit_exceeded")] * 30, {"ok": True})

    assert _get_json(RAW_URL) == {"ok": True}
    assert sleeps == [5.0] * 30


def test_burst_retry_stops_at_the_total_wait_budget(urlopen):
    """Backstop for a server that 429s forever - a CI job must not hang."""
    sleeps, _ = urlopen(*[_http_error(429, "60", "rate_limit_exceeded")] * 1000)

    with pytest.raises(urllib.error.HTTPError):
        _get_json(RAW_URL)

    assert sum(sleeps) >= MAX_TOTAL_BURST_WAIT_SECONDS


@pytest.mark.parametrize("status", [404, 500, 503], ids=["not-found", "server-error", "unavailable"])
def test_non_429_errors_are_not_retried(urlopen, status):
    """A non-429 error must surface rather than be slept on."""
    sleeps, _ = urlopen(_http_error(status))

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get_json(RAW_URL)

    assert exc_info.value.code == status
    assert sleeps == []
