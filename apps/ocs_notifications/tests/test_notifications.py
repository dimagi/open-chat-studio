from unittest.mock import patch

import pytest

from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.notifications import (
    AffectedResources,
    deleted_model_notification,
    deprecated_model_notification,
    message_delivery_failure_notification,
    trace_error_notification,
)
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


class TestMessageDeliveryFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification_without_a_session(self, mock_create_notification):
        """Sends from an early exit (e.g. a disabled channel) have no session. The team still
        has to hear about the failure -- @silence_exceptions would otherwise swallow it whole."""
        experiment = ExperimentFactory.create()

        message_delivery_failure_notification(experiment, None, "WhatsApp", "text message", recipient="+27820001111")

        mock_create_notification.assert_called_once()
        kwargs = mock_create_notification.call_args.kwargs
        assert "+27820001111" in kwargs["message"]
        assert kwargs["links"] == {"View Bot": experiment.get_absolute_url()}


class TestTraceErrorNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_omits_view_trace_link_when_trace_url_is_none(self, mock_create_notification):
        """trace_error_notification omits 'View Trace' link when trace_url is None."""
        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)

        trace_error_notification(
            experiment=experiment,
            session=session,
            span_name="Run Pipeline",
            error_message="Something went wrong",
            permissions=["experiments.change_experiment"],
            trace_url=None,
        )

        call_kwargs = mock_create_notification.call_args.kwargs
        assert "View Trace" not in call_kwargs["links"]

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_slug_derived_from_span_name(self, mock_create_notification):
        """Slug is computed via slugify from span_name."""
        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)

        trace_error_notification(
            experiment=experiment,
            session=session,
            span_name="seed_message",
            error_message="err",
            permissions=None,
            trace_url=None,
        )

        call_kwargs = mock_create_notification.call_args.kwargs
        assert call_kwargs["slug"] == "seed-message"
        assert call_kwargs["title"] == f"Seed Message Failed for '{experiment}'"


def _affected(chatbots=None, pipelines=None, evaluators=None) -> AffectedResources:
    """Build an ``AffectedResources`` with only the kinds a test cares about."""
    return AffectedResources(
        chatbots=chatbots or {},
        pipelines=pipelines or {},
        evaluators=evaluators or {},
    )


class TestAffectedResourcesLinks:
    def test_unique_names_are_not_qualified(self):
        links = _affected(chatbots={"My Bot": "/chatbots/1/"}, evaluators={"Sentiment": "/evaluators/7/"}).links()
        assert links == {"My Bot": "/chatbots/1/", "Sentiment": "/evaluators/7/"}

    def test_name_claimed_by_two_kinds_keeps_both_links(self):
        """Names are unique per kind, so a shared name must not collapse into one link."""
        links = _affected(chatbots={"Support": "/chatbots/1/"}, evaluators={"Support": "/evaluators/7/"}).links()
        assert links == {"Support (chatbot)": "/chatbots/1/", "Support (evaluator)": "/evaluators/7/"}


class TestDeprecatedModelNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_with_replacement(self, mock_create_notification):
        team = TeamFactory.create()
        deprecated_model_notification(
            team=team,
            model_name="gpt-4",
            replacement_model_name="gpt-4o",
            affected=_affected(chatbots={"My Bot": "/chatbots/my-bot/"}),
        )
        mock_create_notification.assert_called_once_with(
            title="LLM Model 'gpt-4' Deprecated",
            message=(
                "The model 'gpt-4' has been deprecated and will be removed soon. "
                "1 chatbot(s) are still using it. "
                "Please update to 'gpt-4o' before it is removed."
            ),
            level=LevelChoices.WARNING,
            team=team,
            slug="llm-model-deprecated",
            event_data={"model_name": "gpt-4"},
            permissions=["service_providers.change_llmprovidermodel"],
            links={"My Bot": "/chatbots/my-bot/"},
            once_per_event_type=True,
        )

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_without_replacement(self, mock_create_notification):
        team = TeamFactory.create()
        deprecated_model_notification(
            team=team,
            model_name="gpt-4",
            replacement_model_name=None,
            affected=_affected(
                pipelines={"My Pipeline": "/pipelines/1/"},
                evaluators={"My Evaluator": "/evaluators/1/"},
            ),
        )
        mock_create_notification.assert_called_once_with(
            title="LLM Model 'gpt-4' Deprecated",
            message=(
                "The model 'gpt-4' has been deprecated and will be removed soon. "
                "1 pipeline(s), 1 evaluator(s) are still using it."
            ),
            level=LevelChoices.WARNING,
            team=team,
            slug="llm-model-deprecated",
            event_data={"model_name": "gpt-4"},
            permissions=["service_providers.change_llmprovidermodel"],
            links={"My Pipeline": "/pipelines/1/", "My Evaluator": "/evaluators/1/"},
            once_per_event_type=True,
        )


class TestDeletedModelNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_with_replacement(self, mock_create_notification):
        team = TeamFactory.create()
        deleted_model_notification(
            team=team,
            model_name="claude-2.0",
            replacement_model_name="claude-3-5-sonnet-latest",
            affected=_affected(chatbots={"Bot A": "/chatbots/a/", "Bot B": "/chatbots/b/"}),
        )
        mock_create_notification.assert_called_once_with(
            title="LLM Model 'claude-2.0' Removed",
            message=(
                "The model 'claude-2.0' has been removed from the platform. "
                "2 chatbot(s) were affected. "
                "References have been automatically updated to use 'claude-3-5-sonnet-latest'."
            ),
            level=LevelChoices.WARNING,
            team=team,
            slug="llm-model-deleted",
            event_data={"model_name": "claude-2.0"},
            permissions=["service_providers.change_llmprovidermodel"],
            links={"Bot A": "/chatbots/a/", "Bot B": "/chatbots/b/"},
            once_per_event_type=True,
        )

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_without_replacement(self, mock_create_notification):
        team = TeamFactory.create()
        deleted_model_notification(
            team=team,
            model_name="claude-2.0",
            replacement_model_name=None,
            affected=_affected(
                pipelines={"Pipeline X": "/pipelines/1/"},
                evaluators={"Evaluator Y": "/evaluators/1/"},
            ),
        )
        mock_create_notification.assert_called_once_with(
            title="LLM Model 'claude-2.0' Removed",
            message=(
                "The model 'claude-2.0' has been removed from the platform. "
                "1 pipeline(s), 1 evaluator(s) were affected. "
                "References have been cleared and will need to be updated manually."
            ),
            level=LevelChoices.WARNING,
            team=team,
            slug="llm-model-deleted",
            event_data={"model_name": "claude-2.0"},
            permissions=["service_providers.change_llmprovidermodel"],
            links={"Pipeline X": "/pipelines/1/", "Evaluator Y": "/evaluators/1/"},
            once_per_event_type=True,
        )
