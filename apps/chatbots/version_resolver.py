"""Resolve a Chatbot Version from a family + a Version Selection Rule.

See `CONTEXT.md` for the domain vocabulary. The rule values mirror the strings
that already live in `evaluations_evaluationconfig.version_selection_type`, so
this enum can be moved without a data migration.

`VersionSelectionRule` supersedes `apps.evaluations.models.ExperimentVersionSelection`
(same string values). The legacy enum is removed and all imports redirected here
in a follow-up commit; until then the two coexist.
"""

from django.db import models


class VersionSelectionRule(models.TextChoices):
    """How a caller asks for a Chatbot Version given a family head."""

    SPECIFIC = "specific", "Specific Version"
    LATEST_WORKING = "latest_working", "Latest Working Version"
    LATEST_PUBLISHED = "latest_published", "Latest Published Version"


class VersionNotFound(ValueError):
    """SPECIFIC was requested but no version with that version_number exists in the family."""


class NoPublishedVersion(ValueError):
    """LATEST_PUBLISHED was requested but the family has no Published Version."""
