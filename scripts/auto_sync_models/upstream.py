"""Layer 3: LiteLLM's price table, translated into the layer-1 shape.

Everything provider-specific lives here. ``model_prices_and_context_window.json``
carries pricing, token limits, provider, model kind and deprecation dates for
~3 900 models, is updated several times a day, and needs no credentials.

Unit conventions: LiteLLM prices per **1** token, the OCS seed per **1 000**.
"""

from __future__ import annotations

import datetime
import enum
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .records import Catalogue, Key, ModelRecord

LITELLM_REPO = "BerriAI/litellm"
LITELLM_PRICING_PATH = "model_prices_and_context_window.json"
PRICING_URL = f"https://raw.githubusercontent.com/{LITELLM_REPO}/refs/heads/main/{LITELLM_PRICING_PATH}"
SOURCE_URL = f"https://github.com/{LITELLM_REPO}/blob/main/{LITELLM_PRICING_PATH}"

USER_AGENT = "ocs-auto-sync-models-script/2.0"

# Worth a second go; anything else is a permission or availability error that
# sleeping will not fix.
RETRYABLE_STATUS = frozenset({429, 503})

# The kinds of model OCS can run as a chatbot. "responses" is the OpenAI
# Responses API, which covers several models OCS already registers.
CHAT_MODES = frozenset({"chat", "responses"})

# Some entries are tagged mode "chat" but emit only audio (Google's lyria music
# models). Where the field is present it is the more reliable signal.
TEXT_MODALITY = "text"

# Price-table capability flag -> the key we record under. LiteLLM carries 43
# supports_* keys; these are the ones that decide a field in model_parameters.py.
PARAM_FLAGS = {
    "supports_reasoning": "reasoning",
    "supports_adaptive_thinking": "adaptive_thinking",
    "supports_sampling_params": "sampling",
    "supports_anthropic_thinking_payload": "thinking_payload",
    "supports_legacy_thinking": "legacy_thinking",
}

# Ordered weakest to strongest; low/medium/high are the unflagged baseline, so
# the table only marks the ends of the range.
EFFORT_LEVELS = ("none", "minimal", "low", "max", "xhigh")

# Price-table cost field -> OCS service kind.
COST_FIELDS = {
    "input_cost_per_token": "llm_input",
    "output_cost_per_token": "llm_output",
    "cache_read_input_token_cost": "llm_cached_input",
    "cache_creation_input_token_cost": "llm_cache_write",
}


class ExtraSegments(enum.Enum):
    """What a key's segments after the provider namespace mean."""

    # A preset, or a pass-through to another vendor. Not a model this provider
    # serves under this name (``perplexity/openai/gpt-5.1``).
    REJECT = "reject"
    # A deployment region or tier, not part of the model name (``azure/eu/gpt-4o``).
    REGION = "region"
    # The originating vendor, which this provider's API expects as part of the
    # model ID (``groq/openai/gpt-oss-120b``).
    MODEL_NAME = "model_name"


@dataclass(frozen=True)
class Namespace:
    """How LiteLLM keys one provider's models, and what OCS calls that provider."""

    ocs_provider: str
    prefix: str
    extra_segments: ExtraSegments = ExtraSegments.REJECT


# LiteLLM tags every entry with the provider that serves it, so which OCS
# providers offer a model is read from the data rather than guessed from the
# organisation that published it. ``prefix`` is the key namespace that provider
# uses, which is not always its tag: Google's AI Studio models are tagged
# "gemini" and keyed "gemini/...", while Vertex models are tagged
# "vertex_ai-language-models" and keyed "vertex_ai/..." or bare.
NAMESPACES: dict[str, Namespace] = {
    "openai": Namespace("openai", "openai"),
    "azure": Namespace("azure", "azure", ExtraSegments.REGION),
    "anthropic": Namespace("anthropic", "anthropic"),
    "gemini": Namespace("google", "gemini"),
    # Vertex tags its gemini models two ways and keys some of them both bare
    # and namespaced, so both tags map to the same OCS provider.
    "vertex_ai-language-models": Namespace("google_vertex_ai", "vertex_ai"),
    "vertex_ai": Namespace("google_vertex_ai", "vertex_ai"),
    "deepseek": Namespace("deepseek", "deepseek"),
    "perplexity": Namespace("perplexity", "perplexity"),
    "groq": Namespace("groq", "groq", ExtraSegments.MODEL_NAME),
    "minimax": Namespace("minimax", "minimax"),
}

PREFIX_BY_OCS_PROVIDER = {ns.ocs_provider: ns.prefix for ns in NAMESPACES.values()}


class UpstreamUnavailable(RuntimeError):
    """The price table could not be read, so the run has nothing to work from."""


def fetch(url: str = PRICING_URL) -> dict[str, Any]:
    """GET the price table, sleeping through a rate limit before giving up."""
    data = _get_with_retry(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}))
    if not data:
        raise UpstreamUnavailable("the LiteLLM price table came back empty")
    return data


def _get_with_retry(request: urllib.request.Request, attempts: int = 3) -> Any:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS:
                raise UpstreamUnavailable(f"could not fetch the LiteLLM price table: {exc}") from exc
            last = exc
            time.sleep(_retry_delay(exc, attempt))
        except Exception as exc:
            raise UpstreamUnavailable(f"could not fetch the LiteLLM price table: {exc}") from exc
    raise UpstreamUnavailable(f"the LiteLLM price table stayed rate-limited over {attempts} attempts: {last}")


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Honour Retry-After when given one, else back off with full jitter."""
    try:
        seconds = float((exc.headers or {}).get("Retry-After", ""))
    except (TypeError, ValueError):
        seconds = 0.0
    return seconds if seconds > 0 else random.uniform(0, 5.0 * (2**attempt))


def translate(data: dict[str, Any], today: datetime.date) -> tuple[Catalogue, Catalogue]:
    """``(live, everything)`` in the layer-1 shape.

    ``live`` holds what OCS could run today: chat-capable and not yet past its
    deprecation date. ``everything`` holds every key that maps to an OCS provider
    at all, filters included, because "LiteLLM no longer lists this model" and
    "LiteLLM lists it as deprecated" are different findings — and because a model
    OCS still serves needs its price compared even once upstream deprecates it.
    """
    live: Catalogue = {}
    everything: Catalogue = {}
    for source_key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        key = _translate_key(source_key, entry.get("litellm_provider"))
        if key is None:
            continue
        record = _record(key, source_key, entry, today)
        _keep_best(everything, key, record)
        if _serves_chat(entry) and not record.deprecated:
            _keep_best(live, key, record)
    return live, everything


def _keep_best(catalogue: Catalogue, key: Key, record: ModelRecord) -> None:
    """Several price-table keys can map to one model; keep the best-ranked one."""
    incumbent = catalogue.get(key)
    if incumbent is None or _key_rank(record.source_key or "", key[0]) < _key_rank(incumbent.source_key or "", key[0]):
        catalogue[key] = record


def _record(key: Key, source_key: str, entry: dict, today: datetime.date) -> ModelRecord:
    provider, name = key
    return ModelRecord(
        provider=provider,
        name=name,
        token_limit=_token_limit(entry),
        rates=rates_from(entry),
        deprecated=_deprecation_passed(entry, today),
        deprecation_date=entry.get("deprecation_date"),
        source_key=source_key,
        params=params_from(entry),
    )


def params_from(entry: dict) -> dict:
    """The model's parameter-relevant capabilities, as the ledger records them.

    A flag the table omits is unknown, not false, so only keys actually present
    are recorded -- ``supports_sampling_params: false`` is what tells us a model
    refuses ``temperature``, and dropping it would lose that.
    """
    params: dict[str, bool | list[str]] = {
        name: bool(entry[flag]) for flag, name in PARAM_FLAGS.items() if flag in entry
    }
    effort_keys = [f"supports_{level}_reasoning_effort" for level in EFFORT_LEVELS]
    if any(key in entry for key in effort_keys):
        params["effort_levels"] = [level for level in EFFORT_LEVELS if entry.get(f"supports_{level}_reasoning_effort")]
    return params


def _translate_key(source_key: str, litellm_provider: str | None) -> Key | None:
    """Map a price-table key to ``(ocs_provider, model_name)``, or None to skip it.

    LiteLLM reuses the segments after its provider namespace for three different
    things, and only ``ExtraSegments`` says which: an Azure region or tier, a
    Perplexity preset or pass-through to another vendor, or - for Groq alone -
    part of the model ID its API expects.
    """
    namespace = NAMESPACES.get(litellm_provider)
    if namespace is None:
        return None
    name = source_key
    prefix = f"{namespace.prefix}/"
    # Upstream self-namespaces some keys ("perplexity/perplexity/sonar").
    while name.startswith(prefix):
        name = name.removeprefix(prefix)
    if not name:
        return None
    if "/" in name:
        if namespace.extra_segments is ExtraSegments.REJECT:
            return None
        if namespace.extra_segments is ExtraSegments.REGION:
            name = name.rsplit("/", 1)[-1]
    return namespace.ocs_provider, name


def _key_rank(source_key: str, ocs_provider: str) -> tuple[int, int]:
    """Lower is better.

    A namespaced key beats a bare one so pricing reads the reseller's own rate,
    and a plain one beats a regional variant of the same model so the seed does
    not inherit a region's premium.
    """
    prefix = f"{PREFIX_BY_OCS_PROVIDER[ocs_provider]}/"
    return (0 if source_key.startswith(prefix) else 1, source_key.count("/"))


def _serves_chat(entry: dict) -> bool:
    if entry.get("mode") not in CHAT_MODES:
        return False
    modalities = entry.get("supported_output_modalities")
    return TEXT_MODALITY in modalities if isinstance(modalities, list) else True


def _deprecation_passed(entry: dict, today: datetime.date) -> bool:
    raw = entry.get("deprecation_date")
    if not raw:
        return False
    try:
        return datetime.date.fromisoformat(str(raw)[:10]) <= today
    except ValueError:
        return False


def _token_limit(entry: dict) -> int | None:
    for field_name in ("max_input_tokens", "max_tokens"):
        value = entry.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def rates_from(entry: dict) -> dict[str, str]:
    """Per-1K rates read off one price-table entry."""
    rates = {}
    for cost_field, service_kind in COST_FIELDS.items():
        price = _per_1k(entry.get(cost_field))
        if price is not None:
            rates[service_kind] = price
    return rates


def _per_1k(per_token: Any) -> str | None:
    """Convert a per-token rate to per-1K, to 8 significant digits."""
    if not isinstance(per_token, int | float) or isinstance(per_token, bool):
        return None
    return str(Decimal(f"{per_token * 1_000.0:.8g}").normalize())
