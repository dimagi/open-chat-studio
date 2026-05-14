import pytest

from apps.chatbots.version_resolver import (
    NoPublishedVersion,
    VersionNotFound,
    VersionSelectionRule,
    resolve_chatbot_version,
    resolve_published_or_working,
)
from apps.utils.factories.experiment import ExperimentFactory


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


@pytest.mark.django_db()
class TestResolveLatestPublished:
    def test_returns_default_version_when_one_exists(self):
        family = ExperimentFactory()
        v1 = family.create_new_version()  # first snapshot becomes default automatically
        result = resolve_chatbot_version(family, VersionSelectionRule.LATEST_PUBLISHED)
        assert result == v1
        assert result.is_default_version

    def test_returns_latest_default_when_promoted(self):
        family = ExperimentFactory()
        family.create_new_version()  # v1 is default
        v2 = family.create_new_version(make_default=True)  # v2 promoted, v1 demoted
        result = resolve_chatbot_version(family, VersionSelectionRule.LATEST_PUBLISHED)
        assert result == v2

    def test_raises_when_family_has_no_snapshots(self):
        family = ExperimentFactory()  # working version only, no snapshots
        with pytest.raises(NoPublishedVersion):
            resolve_chatbot_version(family, VersionSelectionRule.LATEST_PUBLISHED)

    def test_raises_when_snapshots_exist_but_none_is_default(self):
        # Reachable when a previously-default snapshot has been demoted manually
        # (e.g. a team archives or unpublishes the default without promoting another).
        family = ExperimentFactory()
        v1 = family.create_new_version()  # auto-defaulted because version_number == 1
        v1.is_default_version = False
        v1.save(update_fields=["is_default_version"])
        with pytest.raises(NoPublishedVersion):
            resolve_chatbot_version(family, VersionSelectionRule.LATEST_PUBLISHED)


@pytest.mark.django_db()
class TestResolveSpecific:
    def test_returns_snapshot_when_version_number_matches(self):
        family = ExperimentFactory()
        v1 = family.create_new_version()  # snapshot has version_number=1
        family.refresh_from_db()
        result = resolve_chatbot_version(family, VersionSelectionRule.SPECIFIC, version_number=v1.version_number)
        assert result == v1

    def test_returns_family_head_when_version_number_matches_head(self):
        # The family head's own version_number is a valid SPECIFIC target.
        # After create_new_version(), the head's number has incremented past the snapshot's.
        family = ExperimentFactory()
        snapshot = family.create_new_version()
        family.refresh_from_db()
        assert family.version_number != snapshot.version_number  # head advanced past snapshot
        result = resolve_chatbot_version(family, VersionSelectionRule.SPECIFIC, version_number=family.version_number)
        assert result == family

    def test_raises_VersionNotFound_when_version_number_does_not_exist(self):
        family = ExperimentFactory()
        with pytest.raises(VersionNotFound):
            resolve_chatbot_version(family, VersionSelectionRule.SPECIFIC, version_number=999)

    def test_raises_VersionNotFound_when_version_number_belongs_to_other_family(self):
        # Family-membership is implicit in the lookup — a version_number that
        # exists in another family but not this one simply isn't found.
        family_a = ExperimentFactory()
        family_b = ExperimentFactory()
        family_b.create_new_version()  # b_v1
        b_v2 = family_b.create_new_version()  # b_v2 exists in family_b but not family_a
        with pytest.raises(VersionNotFound):
            resolve_chatbot_version(family_a, VersionSelectionRule.SPECIFIC, version_number=b_v2.version_number)

    def test_raises_VersionNotFound_when_specific_called_without_version_number(self):
        family = ExperimentFactory()
        with pytest.raises(VersionNotFound):
            resolve_chatbot_version(family, VersionSelectionRule.SPECIFIC)


@pytest.mark.django_db()
class TestResolvePublishedOrWorking:
    def test_returns_published_when_one_exists(self):
        family = ExperimentFactory()
        v1 = family.create_new_version()  # auto-defaulted
        result = resolve_published_or_working(family)
        assert result == v1

    def test_falls_back_to_working_when_no_published(self):
        family = ExperimentFactory()  # no snapshots
        result = resolve_published_or_working(family)
        assert result == family
        assert result.is_working_version

    def test_falls_back_when_snapshots_exist_but_none_default(self):
        # Reachable when default has been demoted manually
        family = ExperimentFactory()
        v1 = family.create_new_version()
        v1.is_default_version = False
        v1.save(update_fields=["is_default_version"])
        result = resolve_published_or_working(family)
        assert result == family  # working head, not v1
