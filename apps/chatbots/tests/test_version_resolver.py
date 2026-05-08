from apps.chatbots.version_resolver import (
    NoPublishedVersion,
    VersionNotFound,
    VersionSelectionRule,
)


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
