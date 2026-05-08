"""Resolve a Chatbot Version from a family + a Version Selection Rule.

See `CONTEXT.md` for the domain vocabulary. The rule values mirror the strings
that already live in `evaluations_evaluationconfig.version_selection_type`, so
this enum can be moved without a data migration.

`VersionSelectionRule` supersedes `apps.evaluations.models.ExperimentVersionSelection`
(same string values). The legacy enum is removed and all imports redirected here
in a follow-up commit; until then the two coexist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from apps.experiments.models import Experiment


class VersionSelectionRule(models.TextChoices):
    """How a caller asks for a Chatbot Version given a family head."""

    SPECIFIC = "specific", "Specific Version"
    LATEST_WORKING = "latest_working", "Latest Working Version"
    LATEST_PUBLISHED = "latest_published", "Latest Published Version"


class VersionNotFound(ValueError):
    """SPECIFIC was requested but no version with that version_number exists in the family."""


class NoPublishedVersion(ValueError):
    """LATEST_PUBLISHED was requested but the family has no Published Version."""


def resolve_chatbot_version(
    family: Experiment,
    rule: VersionSelectionRule | str,
    version_number: int | None = None,
) -> Experiment:
    """Return the Chatbot Version specified by `rule` within `family`.

    `family` must be the family-head Experiment (i.e. `working_version_id IS NULL`).
    For SPECIFIC, `version_number` is the small int stored on `Experiment.version_number`
    (not a primary key). Raises `VersionNotFound` or `NoPublishedVersion` on failure
    modes — never returns None.
    """
    rule = VersionSelectionRule(rule)

    if not family.is_working_version:
        raise ValueError(
            f"resolve_chatbot_version() requires a family-head Experiment, "
            f"got snapshot {family!r} (working_version_id={family.working_version_id})"
        )

    if rule == VersionSelectionRule.LATEST_WORKING:
        return family.get_working_version()

    raise NotImplementedError(f"Rule {rule} not implemented yet")
