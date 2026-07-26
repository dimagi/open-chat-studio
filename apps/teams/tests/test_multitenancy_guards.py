"""Architecture guard tests for the multi-tenancy contract.

These lock the invariants documented in ``docs/agents/multi_tenancy.md`` so that
changes which silently break team isolation fail in CI rather than in review.

The rule under test: every concrete :class:`~apps.teams.models.BaseTeamModel`
subclass is scoped to a single team via a non-nullable ``team`` FK pointing at
``Team`` with ``on_delete=CASCADE``. A subclass that overrides ``team`` (e.g.
makes it nullable or repoints it) breaks the tenancy guarantee.
"""

from django.apps import apps
from django.db import models

from apps.teams.models import BaseTeamModel, Team

# Models that are intentionally team-optional: system-wide provider defaults that
# are available to every team when ``team`` is null, and team-owned when set.
# Adding to this allowlist is an "Ask first" decision (see AGENTS.md) — a nullable
# ``team`` means rows can be visible across teams, so it must be a deliberate choice.
NULLABLE_TEAM_ALLOWLIST = {
    "service_providers.llmprovidermodel",
    "service_providers.embeddingprovidermodel",
}


def _concrete_team_models() -> list[type[BaseTeamModel]]:
    return [m for m in apps.get_models() if issubclass(m, BaseTeamModel)]


def test_team_models_exist():
    assert _concrete_team_models(), "expected concrete BaseTeamModel subclasses to be discoverable"


def test_team_field_points_at_team():
    problems = []
    for model in _concrete_team_models():
        field = model._meta.get_field("team")
        if not isinstance(field, models.ForeignKey) or field.related_model is not Team:
            problems.append(f"{model._meta.label}: `team` must be a ForeignKey to Team")
    assert not problems, "\n".join(problems)


def test_team_field_cascades_on_delete():
    problems = []
    for model in _concrete_team_models():
        field = model._meta.get_field("team")
        if field.remote_field.on_delete is not models.CASCADE:
            problems.append(f"{model._meta.label}: `team` FK must use on_delete=CASCADE")
    assert not problems, "\n".join(problems)


def test_team_field_non_nullable_outside_allowlist():
    problems = []
    for model in _concrete_team_models():
        field = model._meta.get_field("team")
        if field.null and model._meta.label_lower not in NULLABLE_TEAM_ALLOWLIST:
            problems.append(
                f"{model._meta.label}: `team` FK is nullable. A team-scoped model must require a team; "
                f"if this is deliberately team-optional, add it to NULLABLE_TEAM_ALLOWLIST with a reason."
            )
    assert not problems, "\n".join(problems)


def test_nullable_team_allowlist_has_no_stale_entries():
    labels = {m._meta.label_lower for m in _concrete_team_models()}
    stale = sorted(entry for entry in NULLABLE_TEAM_ALLOWLIST if entry not in labels)
    assert not stale, f"NULLABLE_TEAM_ALLOWLIST references models that no longer exist: {stale}"
