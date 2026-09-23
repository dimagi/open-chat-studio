"""GET /api/v2/chatbots/{id}/versions/status/ -- where the version history stands (#4142)."""

from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.experiments.tasks import async_create_experiment_version
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

from .conftest import status_url, versions_url


def publish(client, chatbot, **body) -> None:
    response = client.post(versions_url(chatbot), body, format="json")
    assert response.status_code == 202, response.content


def poll(client, chatbot):
    return client.get(status_url(chatbot))


def run_the_worker_and_let_it_raise(chatbot) -> None:
    """Run the version task against a snapshot that fails, so its own `finally` frees the lock.

    Driven through the task rather than by calling `release_version_operation_lock` here: what the
    status endpoint is being asked to report is the state the task leaves behind, so a task that
    stopped freeing the lock has to fail these tests.
    """
    with (
        patch.object(Experiment, "create_new_version", side_effect=RuntimeError("the snapshot failed")),
        pytest.raises(RuntimeError),
    ):
        async_create_experiment_version(experiment_id=chatbot.id)


@pytest.mark.django_db()
def test_the_status_url_is_the_registered_route(chatbot):
    assert reverse("api:v2:chatbot-version-status", args=[chatbot.public_id]) == status_url(chatbot)


@pytest.mark.django_db()
class TestInFlight:
    def test_an_operation_that_still_holds_the_lock_is_pending(self, client, chatbot, dispatch):
        """The lock, not Celery's state, is what says "in flight": a task Celery has not started yet
        reads PENDING, which is indistinguishable from a task id it has never heard of."""
        publish(client, chatbot, make_default=True)

        response = poll(client, chatbot)

        assert response.status_code == 200, response.content
        assert response.json() == {"status": "pending"}

    def test_a_revert_holding_the_lock_also_reads_pending(self, client, chatbot):
        """The lock covers every version operation, so `pending` means "an operation is running"
        rather than "your publish is running" -- the same breadth as the publish endpoint's 409."""
        chatbot.acquire_version_operation_lock("revert-1-2")

        assert poll(client, chatbot).json() == {"status": "pending"}


@pytest.mark.django_db()
class TestTerminal:
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_finished_publish_reports_the_version(self, client, chatbot):
        publish(client, chatbot, make_default=True)

        assert poll(client, chatbot).json() == {"status": "completed", "version_number": 1}

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_the_newest_version_is_what_is_reported(self, client, chatbot):
        """The answer is the chatbot's own state, so a second publish moves it on -- there is no
        handle that keeps pointing at the first one."""
        publish(client, chatbot)
        chatbot.name = "Renamed, so there is something to publish"
        chatbot.save(update_fields=["name"])
        publish(client, chatbot, make_default=True)

        assert poll(client, chatbot).json() == {"status": "completed", "version_number": 2}

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_the_report_is_per_chatbot(self, client, chatbot, team):
        """Each chatbot answers for its own history: publishing one leaves the other with nothing
        to report rather than borrowing its neighbour's version."""
        other = ChatbotFactory.create(team=team, name="Other bot")
        publish(client, other, make_default=True)

        assert poll(client, other).json() == {"status": "completed", "version_number": 1}
        assert poll(client, chatbot).status_code == 404


@pytest.mark.django_db()
class TestNothingToReport:
    def test_a_chatbot_that_has_never_published_is_not_found(self, client, chatbot):
        assert poll(client, chatbot).status_code == 404

    def test_a_publish_that_snapshotted_nothing_leaves_nothing_to_report(self, client, chatbot, dispatch):
        """The worker raised before creating anything and its `finally` freed the lock. With no
        version to name and no task to account for, the honest answer is that there is nothing."""
        publish(client, chatbot, make_default=True)
        run_the_worker_and_let_it_raise(chatbot)

        assert poll(client, chatbot).status_code == 404

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_failed_publish_on_a_chatbot_with_history_reports_the_version_already_there(self, client, chatbot):
        """The documented cost of polling the chatbot rather than the task: `completed` names the
        version that was already there, so a client checks `version_number` against the one it
        expected instead of reading `completed` as proof its own snapshot landed."""
        publish(client, chatbot, make_default=True)
        chatbot.refresh_from_db()
        # A second publish the worker raises on. The lock is taken here because the request that
        # would have taken it is refused -- the chatbot is unchanged since v1, so there is nothing
        # to publish -- but freeing it is left to the task, which is the behaviour under test.
        assert chatbot.acquire_version_operation_lock("publish-that-will-raise")
        run_the_worker_and_let_it_raise(chatbot)

        assert poll(client, chatbot).json() == {"status": "completed", "version_number": 1}


@pytest.mark.django_db()
class TestAuth:
    def test_polling_another_teams_chatbot_is_not_found(self, chatbot):
        other = TeamWithUsersFactory.create()

        response = poll(ApiTestClient(other.members.first(), other), chatbot)

        assert response.status_code == 404, response.content

    def test_a_read_only_key_may_poll(self, chatbot, dispatch):
        """Reporting an outcome writes nothing, so the read-only gate lets it through -- an operator
        can watch a publish with the same key that inspects the chatbot."""
        writer = ApiTestClient(chatbot.team.members.first(), chatbot.team)
        publish(writer, chatbot, make_default=True)
        reader = ApiTestClient(chatbot.team.members.first(), chatbot.team, read_only=True)

        assert poll(reader, chatbot).json() == {"status": "pending"}

    def test_a_machine_token_is_held_to_its_applications_chatbot_allowlist(self, chatbot):
        client = ApiTestClient(
            chatbot.team.members.first(),
            chatbot.team,
            auth_method="oauth_client_credentials",
            scopes=["chatbots:read"],
            allowed_chatbots=[],
        )

        assert poll(client, chatbot).status_code == 403
