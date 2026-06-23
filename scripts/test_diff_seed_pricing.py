"""Unit tests for `scripts/diff_seed_pricing.py`.

Run with:  pytest scripts/test_diff_seed_pricing.py -v
"""

from __future__ import annotations

import datetime

import pytest
from diff_seed_pricing import (
    RateChange,
    _format_per_1k,
    _next_migration_number,
    apply_changes,
    compute_changes,
    diffable_models,
    generate_migration,
    rates_from_detail,
    render_pr_body,
    seed_index,
)

# Pure helpers


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


# Diff


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
        """The same upstream model wraps multiple OCS providers (openai+azure).
        A single llm-stats rate change should produce one change per provider.
        """
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


# Apply


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


# PR body


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


def test_render_pr_body_em_dash_for_missing_old_price():
    change = RateChange("openai", "new-model", "llm_input", None, "0.001", "https://llm-stats.com/models/new-model")
    body = render_pr_body([change], unmatched=set())

    assert "| - | 0.001 |" in body
