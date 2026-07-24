"""Expose a masked fingerprint of each LLM provider's API key so a report can
join OCS's key→team ownership against the providers' org-level cost reports.

The provider cost reports never return the secret value — they key rows by a
provider-assigned id. We reproduce each provider's own redaction format (e.g.
``sk-...JrYA`` for OpenAI, ``sk-ant-api03-cLV...lAAA`` for Anthropic) so the
masked key here can be matched against what the provider console/API shows.

Provider ``config`` is encrypted at rest, so we decrypt-on-access per row.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from apps.service_providers.models import LlmProvider
from apps.teams.metadata import get_team_metadata_fields

logger = logging.getLogger("ocs.admin")

_LAST = 4


@dataclass(frozen=True)
class _MaskRule:
    """How to reproduce a provider's own key redaction: keep ``header`` (a
    literal dashed prefix), reveal ``lead`` body chars, then ``...`` + last 4.
    """

    header: str
    lead: int


# Only the providers whose keys carry a stable, non-secret prefix. Everything
# else falls back to ``...<last4>`` with no leading reveal.
_MASK_RULES = {
    "openai": _MaskRule(header="sk-", lead=0),
    "azure": _MaskRule(header="sk-", lead=0),
    "anthropic": _MaskRule(header="sk-ant-api03-", lead=3),
}


def mask_secret(secret: str, provider_type: str) -> str:
    """Redact ``secret`` in the style the given provider uses for its own keys.

    Non-string secrets (e.g. Vertex's service-account JSON dict) have no
    key-style fingerprint to join on, so they mask to an empty string.
    """
    if not isinstance(secret, str) or not secret:
        return ""
    if len(secret) <= _LAST:
        return f"...{secret}"

    last = secret[-_LAST:]
    rule = _MASK_RULES.get(provider_type)
    if rule is None or not secret.startswith(rule.header):
        return f"...{last}"
    body = secret[len(rule.header) :]
    return f"{rule.header}{body[: rule.lead]}...{last}"


def get_provider_key_fingerprints() -> Iterator[dict]:
    """Yield one masked-key record per LLM provider across all teams.

    Each record carries the owning team's `metadata` and `slug` so a report can
    label a team even when it has no usage in the reporting window (and so is
    absent from the usage report, which is keyed on recorded usage).
    """
    metadata_fields = get_team_metadata_fields()
    providers = LlmProvider.objects.select_related("team").order_by("team__name", "type", "name")
    for provider in providers.iterator():
        secret_field = _secret_field_for(provider)
        secret = (provider.config.get(secret_field) or "") if secret_field else ""
        metadata = provider.team.metadata or {}
        yield {
            "team_id": provider.team_id,
            "team_name": provider.team.name,
            "team_slug": provider.team.slug,
            "metadata": {field["key"]: metadata.get(field["key"], "") for field in metadata_fields},
            "provider_id": provider.id,
            "provider_type": provider.type,
            "name": provider.name,
            "masked_key": mask_secret(secret, provider.type),
            "organization": provider.config.get("openai_organization") or None,
        }


def _secret_field_for(provider) -> str | None:
    """The config key holding ``provider``'s API secret, or None if unknown.

    Reuses the form's ``obfuscate_fields`` (the same source of truth the
    provider search uses) so we always mask the field the UI treats as secret.
    """
    obfuscated = _obfuscated_fields(provider)
    return obfuscated[0] if obfuscated else None


def _obfuscated_fields(provider) -> tuple[str, ...]:
    try:
        type_enum = provider.type_enum
    except (KeyError, ValueError):
        logger.warning("Skipping provider id=%s with unrecognised type %r", provider.pk, provider.type)
        return ()
    form_cls = getattr(type_enum, "form_cls", None)
    if form_cls is None:
        return ()
    return tuple(getattr(form_cls, "obfuscate_fields", ()) or ())
