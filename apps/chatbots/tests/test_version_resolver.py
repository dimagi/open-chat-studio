import pytest

from apps.chatbots.version_resolver import (
    NoPublishedVersion,
    VersionNotFound,
    VersionSelectionRule,
    resolve_chatbot_version,
)
from apps.utils.factories.experiment import ExperimentFactory


class TestVersionSelectionRule:
    def test_values_match_legacy_db_strings(self):
        # DB rows in evaluations_evaluationconfig.version_selection_type already use
        # these strings. They MUST stay stable so we don't need a data migration.
        assert VersionSelectionRule.SPECIFIC.value == "specific"
        assert VersionSelectionRule.LATEST_WORKING.value == "latest_working"
        assert VersionSelectionRule.LATEST_PUBLISHED.value == "latest_published"

    def test_labels_are_human_readable(self):
        assert VersionSelectionRule.SPECIFIC.label == "Specific Version"
        assert VersionSelectionRule.LATEST_WORKING.label == "Latest Working Version"
        assert VersionSelectionRule.LATEST_PUBLISHED.label == "Latest Published Version"


class TestExceptionsAreImportable:
    def test_exceptions_subclass_value_error(self):
        # Catch-all callers that don't want to discriminate between failure modes
        # can `except ValueError`.
        assert issubclass(VersionNotFound, ValueError)
        assert issubclass(NoPublishedVersion, ValueError)


@pytest.mark.django_db()
class TestResolveLatestWorking:
    def test_returns_family_head_when_called_on_family_head(self):
        family = ExperimentFactory()  # working version, no snapshots
        result = resolve_chatbot_version(family, VersionSelectionRule.LATEST_WORKING)
        assert result == family

    def test_returns_family_head_when_family_has_snapshots(self):
        family = ExperimentFactory()
        family.create_new_version()  # makes a snapshot, family head still editable
        result = resolve_chatbot_version(family, VersionSelectionRule.LATEST_WORKING)
        assert result == family
        assert result.is_working_version

    def test_raises_when_family_is_a_snapshot(self):
        family = ExperimentFactory()
        snapshot = family.create_new_version()
        with pytest.raises(ValueError, match="family-head"):
            resolve_chatbot_version(snapshot, VersionSelectionRule.LATEST_WORKING)
