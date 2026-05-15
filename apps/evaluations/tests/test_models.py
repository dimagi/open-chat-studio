from unittest.mock import patch

import pytest

from apps.chatbots.version_resolver import NoPublishedVersion, VersionSelectionRule
from apps.utils.factories.evaluations import EvaluationConfigFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
def test_get_generation_experiment_version_specific():
    experiment = ExperimentFactory.create()
    config = EvaluationConfigFactory.create(
        experiment_version=experiment,
        base_experiment=experiment,
        version_selection_type=VersionSelectionRule.SPECIFIC,
    )
    assert config.get_generation_experiment_version() == experiment


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_working():
    working_experiment = ExperimentFactory.create()
    working_experiment.create_new_version("test")

    config = EvaluationConfigFactory.create(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=VersionSelectionRule.LATEST_WORKING,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version() == working_experiment


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_published():
    working_experiment = ExperimentFactory.create()
    working_experiment.create_new_version("test", make_default=True)
    published_version = working_experiment.create_new_version("test", make_default=True)

    config = EvaluationConfigFactory.create(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=VersionSelectionRule.LATEST_PUBLISHED,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version() == published_version


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_published_none():
    """When there are no published versions and LATEST_PUBLISHED is requested, the resolver raises."""

    working_experiment = ExperimentFactory.create()
    config = EvaluationConfigFactory.create(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=VersionSelectionRule.LATEST_PUBLISHED,
        team=working_experiment.team,
    )
    with pytest.raises(NoPublishedVersion):
        config.get_generation_experiment_version()


@pytest.mark.django_db()
def test_get_generation_experiment_version_no_base_experiment():
    config = EvaluationConfigFactory.create(
        experiment_version=None,
        base_experiment=None,
        version_selection_type=VersionSelectionRule.LATEST_WORKING,
    )
    assert config.get_generation_experiment_version() is None


@pytest.mark.django_db()
class TestEvaluationConfigDelegatesToResolver:
    def test_specific_short_circuits_to_stored_fk(self):
        # SPECIFIC bypasses the resolver entirely — the FK is the answer.
        family = ExperimentFactory()
        v1 = family.create_new_version()
        config = EvaluationConfigFactory(
            experiment_version=v1,
            version_selection_type=VersionSelectionRule.SPECIFIC,
            team=family.team,
        )

        with patch("apps.evaluations.models.resolve_chatbot_version") as mock_resolve:
            result = config.get_generation_experiment_version()

        mock_resolve.assert_not_called()
        assert result == v1

    def test_latest_published_delegates_to_resolver(self):
        family = ExperimentFactory()
        v1 = family.create_new_version()  # auto-defaults
        config = EvaluationConfigFactory(
            base_experiment=family,
            version_selection_type=VersionSelectionRule.LATEST_PUBLISHED,
            team=family.team,
        )

        with patch(
            "apps.evaluations.models.resolve_chatbot_version",
            return_value=v1,
        ) as mock_resolve:
            result = config.get_generation_experiment_version()

        mock_resolve.assert_called_once_with(family, VersionSelectionRule.LATEST_PUBLISHED)
        assert result == v1
