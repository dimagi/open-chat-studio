"""
Unit tests for scripts/fetch_model_updates.py.

Run with:  python scripts/test_fetch_model_updates.py
or:        pytest scripts/test_fetch_model_updates.py -v
"""

from __future__ import annotations

import json
import textwrap
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

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


class TestUnitConversions(unittest.TestCase):
    def test_per_million_to_per_1k(self):
        # $2.50 per million → $0.0025 per 1K
        self.assertAlmostEqual(_per_million_to_per_1k(2.5), 0.0025)

    def test_per_million_to_per_1k_none(self):
        self.assertIsNone(_per_million_to_per_1k(None))

    def test_per_token_to_per_1k(self):
        # $0.0000025 per token → $0.0025 per 1K
        self.assertAlmostEqual(_per_token_to_per_1k(0.0000025), 0.0025)

    def test_per_token_to_per_1k_none(self):
        self.assertIsNone(_per_token_to_per_1k(None))


class TestFmt(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(_fmt(None))

    def test_strips_trailing_zeros(self):
        # 0.00250 → "0.0025"
        result = _fmt(0.00250)
        self.assertEqual(result, "0.0025")

    def test_small_value(self):
        # 0.000075 → "0.000075"
        result = _fmt(0.000075)
        self.assertEqual(result, "0.000075")

    def test_precision_gpt4o_input(self):
        # $2.50/M → $0.0025/1K
        result = _fmt(_per_million_to_per_1k(2.5))
        self.assertEqual(result, "0.0025")

    def test_precision_gpt4o_output(self):
        # $10.00/M → $0.01/1K
        result = _fmt(_per_million_to_per_1k(10.0))
        self.assertEqual(result, "0.01")

    def test_decimal_roundtrip(self):
        # Verify result can be parsed back as Decimal cleanly (no float noise)
        val = _fmt(_per_token_to_per_1k(0.000000075))
        self.assertIsNotNone(val)
        d = Decimal(val)
        self.assertGreater(d, 0)


# ---------------------------------------------------------------------------
# Pricing resolution
# ---------------------------------------------------------------------------


class TestResolvePricingFromLlmStats(unittest.TestCase):
    def _details(self, **kwargs):
        return kwargs

    def test_full_pricing(self):
        details = self._details(
            input_price=2.5,
            output_price=10.0,
            cached_input_price=1.25,
            cache_write_price=3.75,
        )
        result = resolve_pricing_from_llm_stats(details)
        self.assertIsNotNone(result)
        self.assertEqual(result["llm_input"], "0.0025")
        self.assertEqual(result["llm_output"], "0.01")
        self.assertEqual(result["llm_cached_input"], "0.00125")
        self.assertEqual(result["llm_cache_write"], "0.00375")

    def test_no_pricing_returns_none(self):
        self.assertIsNone(resolve_pricing_from_llm_stats({}))

    def test_partial_pricing_no_output(self):
        # input present, output missing → still returns a result
        details = self._details(input_price=1.0)
        result = resolve_pricing_from_llm_stats(details)
        self.assertIsNotNone(result)
        self.assertIn("llm_input", result)
        self.assertNotIn("llm_output", result)

    def test_both_missing_returns_none(self):
        details = self._details(cached_input_price=0.5)
        # cached_input_price alone without input/output → None
        self.assertIsNone(resolve_pricing_from_llm_stats(details))

    def test_unit_conversion_anthropic_claude(self):
        # claude-sonnet-4-6: $3/M input, $15/M output
        details = self._details(input_price=3.0, output_price=15.0)
        result = resolve_pricing_from_llm_stats(details)
        self.assertEqual(result["llm_input"], "0.003")
        self.assertEqual(result["llm_output"], "0.015")


class TestResolvePricingFromLiteLLM(unittest.TestCase):
    def _litellm_entry(self, **kwargs):
        return kwargs

    def test_full_pricing(self):
        litellm_data = {
            "gpt-4o": self._litellm_entry(
                input_cost_per_token=0.0000025,
                output_cost_per_token=0.00001,
                cache_read_input_token_cost=0.00000125,
            )
        }
        result = resolve_pricing_from_litellm("gpt-4o", litellm_data)
        self.assertIsNotNone(result)
        self.assertEqual(result["llm_input"], "0.0025")
        self.assertEqual(result["llm_output"], "0.01")
        self.assertEqual(result["llm_cached_input"], "0.00125")

    def test_missing_model_returns_none(self):
        self.assertIsNone(resolve_pricing_from_litellm("unknown-model", {}))

    def test_entry_without_cost_fields_returns_none(self):
        litellm_data = {"gpt-4o": {"context_window": 128000}}
        self.assertIsNone(resolve_pricing_from_litellm("gpt-4o", litellm_data))

    def test_per_token_conversion(self):
        # $0.000003/token input → $0.003/1K
        litellm_data = {
            "claude-sonnet": {
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000015,
            }
        }
        result = resolve_pricing_from_litellm("claude-sonnet", litellm_data)
        self.assertEqual(result["llm_input"], "0.003")
        self.assertEqual(result["llm_output"], "0.015")


# ---------------------------------------------------------------------------
# build_pricing_entries
# ---------------------------------------------------------------------------


class TestBuildPricingEntries(unittest.TestCase):
    def test_single_provider(self):
        pricing = {"llm_input": "0.0025", "llm_output": "0.01"}
        entries = build_pricing_entries("my-model", ["openai"], pricing)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["provider_type"], "openai")
        self.assertEqual(entry["model_name"], "my-model")
        self.assertEqual(len(entry["rules"]), 2)
        rules_by_kind = {r["service_kind"]: r["unit_price"] for r in entry["rules"]}
        self.assertEqual(rules_by_kind["llm_input"], "0.0025")
        self.assertEqual(rules_by_kind["llm_output"], "0.01")

    def test_multi_provider(self):
        pricing = {"llm_input": "0.0025", "llm_output": "0.01"}
        entries = build_pricing_entries("my-model", ["openai", "azure"], pricing)
        self.assertEqual(len(entries), 2)
        provider_types = {e["provider_type"] for e in entries}
        self.assertEqual(provider_types, {"openai", "azure"})

    def test_empty_providers(self):
        entries = build_pricing_entries("x", [], {"llm_input": "0.01"})
        self.assertEqual(entries, [])

    def test_empty_pricing_skipped(self):
        # No rules → no entries
        entries = build_pricing_entries("x", ["openai"], {})
        self.assertEqual(entries, [])


# ---------------------------------------------------------------------------
# load_registered_models
# ---------------------------------------------------------------------------

SAMPLE_DEFAULT_MODELS = textwrap.dedent('''\
    DEFAULT_LLM_PROVIDER_MODELS = {
        "openai": [
            Model("gpt-4o", 128000),
            Model("gpt-4o-mini", 128000, is_default=True),
            Model("gpt-4", k(8), deprecated=True),
        ],
        "anthropic": [
            Model("claude-sonnet-4-6", 1000000, is_default=True),
            Model("claude-opus-4-6", k(200)),
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
    ]
''')


class TestLoadRegisteredModels(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        models_path = self.tmp / "apps/service_providers/llm_service"
        models_path.mkdir(parents=True)
        (models_path / "default_models.py").write_text(SAMPLE_DEFAULT_MODELS)

    def tearDown(self):
        self._tmp.cleanup()

    def test_registered_openai(self):
        registered = load_registered_models(self.tmp)
        self.assertIn("openai", registered)
        self.assertIn("gpt-4o", registered["openai"])
        self.assertIn("gpt-4o-mini", registered["openai"])
        self.assertIn("gpt-4", registered["openai"])

    def test_registered_anthropic(self):
        registered = load_registered_models(self.tmp)
        self.assertIn("claude-sonnet-4-6", registered["anthropic"])
        self.assertIn("claude-opus-4-6", registered["anthropic"])

    def test_registered_google_vertex(self):
        registered = load_registered_models(self.tmp)
        self.assertIn("gemini-2.5-flash", registered["google"])
        self.assertIn("gemini-2.5-flash", registered["google_vertex_ai"])

    def test_deleted_models_captured(self):
        registered = load_registered_models(self.tmp)
        # DELETED_MODELS entries are captured under their provider
        self.assertIn("gpt-4", registered.get("azure", set()))
        self.assertIn("gpt-35-turbo", registered.get("azure", set()))
        self.assertIn("claude-2.0", registered.get("anthropic", set()))

    def test_unknown_provider_not_present(self):
        registered = load_registered_models(self.tmp)
        self.assertNotIn("deepseek", registered)


# ---------------------------------------------------------------------------
# load_priced_models
# ---------------------------------------------------------------------------

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


class TestLoadPricedModels(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        pricing_dir = self.tmp / "apps/cost_tracking/seed_data"
        pricing_dir.mkdir(parents=True)
        (pricing_dir / "llm_pricing.json").write_text(json.dumps(SAMPLE_PRICING))

    def tearDown(self):
        self._tmp.cleanup()

    def test_priced_pairs(self):
        priced = load_priced_models(self.tmp)
        self.assertIn(("openai", "gpt-4o"), priced)
        self.assertIn(("anthropic", "claude-sonnet-4-6"), priced)
        self.assertNotIn(("openai", "claude-sonnet-4-6"), priced)


# ---------------------------------------------------------------------------
# process_candidates (integration-ish)
# ---------------------------------------------------------------------------


class TestProcessCandidates(unittest.TestCase):
    def _candidate(self, model_id, org, context_window=128000, details=None):
        return {
            "id": model_id,
            "organization": {"id": org},
            "model_type": "llm",
            "context_window": context_window,
            "details": details or {
                "input_price": 2.5,
                "output_price": 10.0,
                "url": f"https://llm-stats.com/models/{model_id}",
                "sources": {},
            },
        }

    def test_new_model_with_pricing(self):
        candidates = [self._candidate("gpt-new", "openai")]
        registered = {"openai": set(), "azure": set()}
        priced: set = set()
        result = process_candidates(candidates, registered, priced, {})

        self.assertEqual(len(result["new_models"]), 1)
        self.assertEqual(len(result["already_registered"]), 0)
        m = result["new_models"][0]
        self.assertTrue(m["pricing"]["has_pricing"])
        self.assertEqual(m["pricing"]["source"], "llm_stats")
        # Both openai and azure should get pricing entries
        providers_in_entries = {
            e["provider_type"] for e in m["pricing"]["llm_pricing_entries"]
        }
        self.assertEqual(providers_in_entries, {"openai", "azure"})

    def test_fully_registered_model_is_skipped(self):
        candidates = [self._candidate("gpt-4o", "openai")]
        registered = {"openai": {"gpt-4o"}, "azure": {"gpt-4o"}}
        result = process_candidates(candidates, registered, set(), {})
        self.assertEqual(len(result["new_models"]), 0)
        self.assertEqual(len(result["already_registered"]), 1)
        self.assertEqual(result["already_registered"][0]["id"], "gpt-4o")

    def test_partially_registered_model_still_processed(self):
        # Registered in openai but not azure → still a new_model (needs azure entry)
        candidates = [self._candidate("gpt-4o", "openai")]
        registered = {"openai": {"gpt-4o"}, "azure": set()}
        result = process_candidates(candidates, registered, set(), {})
        self.assertEqual(len(result["new_models"]), 1)

    def test_unpriced_model_flagged(self):
        candidate = self._candidate("mystery-model", "deepseek", details={
            "url": "https://llm-stats.com/models/mystery-model",
            "sources": {},
            # No pricing fields
        })
        result = process_candidates([candidate], {"deepseek": set()}, set(), {})
        self.assertEqual(len(result["unpriced_models"]), 1)
        self.assertFalse(result["new_models"][0]["pricing"]["has_pricing"])

    def test_litellm_fallback_used_when_llm_stats_missing(self):
        candidate = self._candidate("claude-3", "anthropic", details={
            "url": "https://llm-stats.com/models/claude-3",
            "sources": {},
            # No pricing in llm_stats details
        })
        litellm_data = {
            "claude-3": {
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000015,
            }
        }
        result = process_candidates(
            [candidate], {"anthropic": set()}, set(), litellm_data
        )
        m = result["new_models"][0]
        self.assertTrue(m["pricing"]["has_pricing"])
        self.assertEqual(m["pricing"]["source"], "litellm")
        self.assertEqual(m["pricing"]["rates"]["llm_input"], "0.003")

    def test_already_priced_providers_excluded_from_new_entries(self):
        candidates = [self._candidate("gpt-4o", "openai")]
        registered = {"openai": set(), "azure": set()}
        # Already priced for openai, not azure
        priced = {("openai", "gpt-4o")}
        result = process_candidates(candidates, registered, priced, {})
        m = result["new_models"][0]
        self.assertEqual(m["already_priced_providers"], ["openai"])
        entry_providers = {
            e["provider_type"] for e in m["pricing"]["llm_pricing_entries"]
        }
        # Only azure should get a new pricing entry
        self.assertEqual(entry_providers, {"azure"})

    def test_pricing_entries_flat_list_on_output(self):
        candidates = [
            self._candidate("model-a", "anthropic"),
            self._candidate("model-b", "google"),
        ]
        registered = {"anthropic": set(), "google": set(), "google_vertex_ai": set()}
        result = process_candidates(candidates, registered, set(), {})
        # anthropic: 1 provider, google: 2 providers → 3 entries total
        self.assertEqual(len(result["pricing_entries"]), 3)

    def test_details_error_falls_back_to_litellm(self):
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
        result = process_candidates(
            [candidate], {"openai": set(), "azure": set()}, set(), litellm_data
        )
        m = result["new_models"][0]
        self.assertTrue(m["pricing"]["has_pricing"])
        self.assertEqual(m["pricing"]["source"], "litellm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
