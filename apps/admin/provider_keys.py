"""Expose a masked fingerprint of each LLM provider's API key so a report can
join OCS's key→team ownership against the providers' org-level cost reports.

The provider cost reports never return the secret value — they key rows by a
provider-assigned id. We reproduce each provider's own redaction format (e.g.
``sk-...JrYA`` for OpenAI, ``sk-ant-api03-cLV...lAAA`` for Anthropic) so the
masked key here can be matched against what the provider console/API shows.

Not every provider bills against something a key can identify. Vertex authenticates
with a service-account document rather than an API key, and it bills by GCP project;
tracing services bill by their own project inside an organization. Those providers
carry a non-secret project identifier instead, which is the join their cost export
actually keys on.

Provider ``config`` is encrypted at rest, so we decrypt-on-access per row.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass

from apps.service_providers.models import LlmProvider, LlmProviderTypes, TraceProvider
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


def get_provider_key_fingerprints(metadata_fields: list[dict] | None = None) -> Iterator[dict]:
    """Yield one masked-key record per LLM provider across all teams.

    Each record carries the owning team's `metadata` and `slug` so a report can
    label a team even when it has no usage in the reporting window (and so is
    absent from the usage report, which is keyed on recorded usage).

    ``metadata_fields`` is accepted so a caller emitting several of these listings in
    one response validates the setting once instead of per listing.
    """
    if metadata_fields is None:
        metadata_fields = get_team_metadata_fields()
    providers = LlmProvider.objects.select_related("team").order_by("team__name", "type", "name")
    for provider in providers.iterator():
        secret_field = _secret_field_for(provider)
        secret = (provider.config.get(secret_field) or "") if secret_field else ""
        yield {
            "team_id": provider.team_id,
            "team_name": provider.team.name,
            "team_slug": provider.team.slug,
            "metadata": _team_metadata(provider.team, metadata_fields),
            "provider_id": provider.id,
            "provider_type": provider.type,
            "name": provider.name,
            "masked_key": mask_secret(secret, provider.type),
            "organization": provider.config.get("openai_organization") or None,
            "cloud_project": _cloud_project(provider),
        }


def get_trace_provider_records(metadata_fields: list[dict] | None = None) -> Iterator[dict]:
    """Yield one record per tracing provider across all teams.

    Tracing bills per ingested unit at the *organization* level, so the join a spend
    report needs is project → team rather than key → team. ``TraceProvider.metadata``
    already holds the Langfuse project and organization — recorded on save, or by the
    ``backfill_langfuse_metadata`` command — and is deliberately unencrypted so usage
    can be aggregated by it. ``host`` and ``organization_id`` are what distinguish a
    team tracing into our own organization (and so onto our invoice) from one bringing
    its own account, which no report of ours should charge for.

    Carries no secret: the key pair stays in the encrypted config.
    """
    if metadata_fields is None:
        metadata_fields = get_team_metadata_fields()
    providers = TraceProvider.objects.select_related("team").order_by("team__name", "type", "name")
    for provider in providers.iterator():
        provider_metadata = provider.metadata or {}
        yield {
            "team_id": provider.team_id,
            "team_name": provider.team.name,
            "team_slug": provider.team.slug,
            "metadata": _team_metadata(provider.team, metadata_fields),
            "provider_id": provider.id,
            "provider_type": provider.type,
            "name": provider.name,
            "host": provider.config.get("host") or "",
            "project_id": provider_metadata.get("project_id") or "",
            "project_name": provider_metadata.get("project_name") or "",
            "organization_id": provider_metadata.get("organization_id") or "",
            "organization_name": provider_metadata.get("organization_name") or "",
        }


def _team_metadata(team, metadata_fields) -> dict:
    metadata = team.metadata or {}
    return {field["key"]: metadata.get(field["key"], "") for field in metadata_fields}


def _cloud_project(provider) -> str | None:
    """The cloud project ``provider`` bills to, or None if it doesn't bill by project.

    Vertex authenticates with a service-account document rather than an API key, so
    ``mask_secret`` has nothing to fingerprint and the key→team join can never fire for
    it. Its GCP project id is the join instead — that is what the Cloud Billing export
    keys cost by — and it is an identifier, not a credential.

    Gated on the provider type rather than on the presence of ``credentials_json``:
    ``config`` is a free-form JSON blob, so any provider could carry that key, and a
    stray one would hand a spend report a project id to attribute cost by — quietly
    billing the wrong team.
    """
    if provider.type != str(LlmProviderTypes.google_vertex_ai):
        return None
    credentials = provider.config.get("credentials_json")
    project = _as_dict(credentials).get("project_id") or None
    if credentials and not project:
        # Configured but unusable -- malformed JSON, or a document with no project_id.
        # Either way this team's Vertex spend cannot be joined, so say so rather than
        # letting it silently fall through to the pro-rata bucket.
        logger.warning("Vertex provider id=%s has no usable project_id in credentials_json", provider.pk)
    return project


def _as_dict(value) -> dict:
    """``value`` as a dict, whether it is stored as one or as a JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


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
