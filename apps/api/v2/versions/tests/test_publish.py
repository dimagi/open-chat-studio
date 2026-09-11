"""POST /api/v2/chatbots/{id}/versions/ -- publish a snapshot of the working version (#4142)."""

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.pipelines.build_state import pipeline_build_state

from .conftest import nodes_url, strand_the_end_node, versions_url


def publish(client, chatbot, **body):
    return client.post(versions_url(chatbot), body, format="json")


@pytest.mark.django_db()
def test_the_publish_url_is_the_registered_route(chatbot):
    assert reverse("api:v2:chatbot-version-create", args=[chatbot.public_id]) == versions_url(chatbot)


@pytest.mark.django_db()
class TestDispatch:
    def test_publishing_is_accepted_with_nothing_handed_back(self, client, chatbot, dispatch):
        """202, not 201: the snapshot is taken by a worker, so nothing exists to point at yet. And
        no body, because the status endpoint takes no handle -- there is nothing to give."""
        response = publish(client, chatbot, make_default=True)

        assert response.status_code == 202, response.content
        assert not response.content
        assert dispatch.call_count == 1

    def test_the_task_runs_under_the_lock_token(self, client, chatbot, dispatch):
        """One uuid is both the lock token and the Celery task id. Nothing hands it out any more,
        but the task's `finally` releases the lock by experiment id, so the two have to stay the
        one value for a publish to be visible to a poll at all."""
        publish(client, chatbot, make_default=True)

        chatbot.refresh_from_db()
        assert dispatch.call_args.kwargs["task_id"] == chatbot.create_version_task_id

    def test_the_body_reaches_the_task(self, client, chatbot, dispatch):
        publish(client, chatbot, make_default=True, version_description="Added the 24h timeout")

        assert dispatch.call_args.kwargs["kwargs"] == {
            "experiment_id": chatbot.id,
            "version_description": "Added the 24h timeout",
            "make_default": True,
        }

    def test_an_empty_body_checkpoints_without_going_live(self, client, chatbot, dispatch):
        """The defaults are the cautious ones: an agent that omits `make_default` snapshots its work
        rather than swapping the version live channels serve."""
        assert publish(client, chatbot).status_code == 202

        assert dispatch.call_args.kwargs["kwargs"] == {
            "experiment_id": chatbot.id,
            "version_description": "",
            "make_default": False,
        }

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_publish_that_runs_produces_the_version_and_frees_the_lock(self, client, chatbot):
        """End to end, with no stub between the endpoint and the snapshot it asked for."""
        assert publish(client, chatbot, make_default=True, version_description="v1").status_code == 202

        version = chatbot.versions.get()
        assert (version.version_number, version.is_default_version) == (1, True)
        assert version.version_description == "v1"
        chatbot.refresh_from_db()
        assert not chatbot.version_operation_in_progress

    def test_a_second_operation_while_one_is_in_flight_is_refused(self, client, chatbot, dispatch):
        """The lock guards every version operation, a revert included, so the answer names neither
        a task to poll nor the operation holding it -- the caller retries or reads the versions back."""
        chatbot.acquire_version_operation_lock("already-running")

        response = publish(client, chatbot, make_default=True)

        assert response.status_code == 409
        assert "in progress" in response.json()["detail"]
        dispatch.assert_not_called()
        chatbot.refresh_from_db()
        assert chatbot.create_version_task_id == "already-running"


@pytest.mark.django_db()
class TestGoLiveGate:
    """Going live is gated on the pipeline validating; checkpointing a draft is not.

    The divergence from the UI, which lets a human publish a bot whose red markers they can see:
    live channels resolve the default version per message, so `make_default` on a broken pipeline
    breaks every conversation, and an unattended agent has nothing to look at.
    """

    def test_a_broken_pipeline_cannot_be_made_live(self, client, chatbot, dispatch):
        strand_the_end_node(chatbot)

        response = publish(client, chatbot, make_default=True)

        assert response.status_code == 422
        assert response.json()["pipeline_errors"] == pipeline_build_state(chatbot.pipeline)["errors"]
        dispatch.assert_not_called()
        chatbot.refresh_from_db()
        assert not chatbot.version_operation_in_progress

    def test_the_refusal_reports_the_errors_that_caused_it(self, client, chatbot, dispatch):
        """Not just that it failed: the errors are the repair list, so the agent's next call is a
        fix rather than a re-read."""
        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")
        node_id = response.json()["node"]["node_id"]

        errors = publish(client, chatbot, make_default=True).json()["pipeline_errors"]

        assert "llm_provider_id" in errors["node"][node_id]

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_broken_pipeline_can_still_be_checkpointed_once_something_is_live(self, client, chatbot):
        """Lenient where it costs nothing: a snapshot nothing serves cannot break a conversation.

        Only true from the second version on, which is why a version is published first: until then
        the snapshot *is* what participants get.
        """
        assert publish(client, chatbot, make_default=True).status_code == 202
        # An LLM node with no provider: a real change, and one that invalidates the graph.
        added = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")
        assert added.json()["pipeline_valid"] is False

        assert publish(client, chatbot).status_code == 202
        assert chatbot.versions.count() == 2

    def test_a_broken_first_publish_is_refused_even_without_make_default(self, client, chatbot, dispatch):
        """`create_new_version` makes the first version the default whatever `make_default` says --
        a chatbot with versions but no default would serve nothing -- so there is no such thing as
        checkpointing a first draft. Ungated, this published a broken pipeline straight to
        participants on a `make_default: false` request.
        """
        strand_the_end_node(chatbot)

        response = publish(client, chatbot)

        assert response.status_code == 422, response.content
        assert response.json()["pipeline_errors"] == pipeline_build_state(chatbot.pipeline)["errors"]
        dispatch.assert_not_called()
        assert chatbot.versions.count() == 0

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_checkpointed_first_version_is_the_one_participants_are_served(self, client, chatbot):
        """The fact the gate above rests on, pinned directly so it cannot drift out from under it."""
        assert publish(client, chatbot).status_code == 202

        assert chatbot.versions.get().is_default_version is True

    def test_unwired_handles_never_block_going_live(self, client, chatbot, dispatch, llm_provider):
        """A node wired to nothing is a normal state mid-build, so it is advisory and not an error.
        The gate rejects on the errors report and nothing else."""
        provider, model = llm_provider
        island = client.post(
            nodes_url(chatbot),
            {
                "type": "LLMResponseWithPrompt",
                "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
            },
            format="json",
        ).json()
        assert island["pipeline_valid"] is True  # guards the guard
        assert island["unwired_handles"], "the island has to be reported unwired for this to test anything"

        assert publish(client, chatbot, make_default=True).status_code == 202

    def test_a_chatbot_with_no_pipeline_is_not_gated(self, client, team, dispatch):
        """`Experiment.pipeline` is nullable, and rows predating pipeline-backed chatbots hold null.
        There is nothing to validate, so there is nothing to refuse -- as in the UI."""
        chatbot = Experiment.objects.create(team=team, name="Legacy", owner=team.members.first())

        assert publish(client, chatbot, make_default=True).status_code == 202


@pytest.mark.django_db()
class TestNothingToPublish:
    """A working version identical to the newest one is refused rather than snapshotted.

    The same comparison the web app's "unreleased changes" badge is drawn from, so an agent is held
    to the rule a person sees: a version records a change, and there is no change to record.
    """

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_republishing_an_unchanged_chatbot_is_refused(self, client, chatbot):
        assert publish(client, chatbot, make_default=True).status_code == 202

        response = publish(client, chatbot, make_default=True)

        assert response.status_code == 422
        assert "no changes since its newest version" in response.json()["detail"]
        assert "pipeline_errors" not in response.json()
        assert chatbot.versions.count() == 1

    def test_the_first_publish_is_never_refused(self, client, chatbot, dispatch):
        """`compare_with_latest()` reports False for a chatbot with no versions, so "no baseline"
        and "no difference" arrive as the same value -- the absence of versions has to be checked
        separately or a chatbot could never be published at all."""
        assert publish(client, chatbot, make_default=True).status_code == 202
        assert dispatch.call_count == 1

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_settings_change_since_the_last_version_may_be_published(self, client, chatbot):
        publish(client, chatbot, make_default=True)

        chatbot.name = "Support bot, renamed"
        chatbot.save(update_fields=["name"])

        assert publish(client, chatbot, make_default=True).status_code == 202

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_pipeline_edit_since_the_last_version_may_be_published(self, client, chatbot, llm_provider):
        """The edit this API is mostly used for. A node added through the façade has to register as
        a difference, or every publish after the first would be refused."""
        publish(client, chatbot, make_default=True)

        provider, model = llm_provider
        added = client.post(
            nodes_url(chatbot),
            {
                "type": "LLMResponseWithPrompt",
                "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
            },
            format="json",
        )
        assert added.status_code == 201, added.content

        assert publish(client, chatbot, make_default=True).status_code == 202

    @pytest.mark.parametrize(
        "rewire",
        [
            pytest.param("delete", id="unwiring-an-edge"),
            pytest.param("create", id="wiring-one-up"),
        ],
    )
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_rewiring_since_the_last_version_may_be_published(self, client, chatbot, rewire):
        """Edges live in `Pipeline.data` rather than in a row of their own, so the version
        comparison was once blind to them and a client that only rewires the graph could never
        publish again. `Pipeline`'s version details cover the wiring now.
        """
        publish(client, chatbot, make_default=True)

        edges_url = f"/api/v2/chatbots/{chatbot.public_id}/pipeline/edges/"
        edge = chatbot.pipeline.data["edges"][0]
        unwired = client.delete(f"{edges_url}{edge['id']}/")
        assert unwired.status_code == 200, unwired.content
        if rewire == "create":
            # Bank the unwiring first, so the change left to detect is the wire coming back.
            assert publish(client, chatbot).status_code == 202
            rewired = client.post(
                edges_url, {"wires": [{"source": edge["source"], "target": edge["target"]}]}, format="json"
            )
            assert rewired.status_code == 201, rewired.content
        chatbot.refresh_from_db()
        # The rewiring is the only change, so this is what the refusal is asked to read.
        assert chatbot.compare_with_latest() is True

        assert publish(client, chatbot).status_code == 202

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_the_diff_is_checked_before_the_pipeline_gate(self, client, chatbot):
        """Asked in the order the cheaper question comes first: an unchanged chatbot is refused for
        having nothing to publish, without the graph being walked to find fault with it too.

        Reaching that state takes a checkpoint of the broken graph, since a chatbot whose pipeline
        broke since its last version has, by that very fact, something to publish.
        """
        publish(client, chatbot, make_default=True)
        strand_the_end_node(chatbot)
        assert publish(client, chatbot).status_code == 202

        response = publish(client, chatbot, make_default=True)

        assert response.status_code == 422
        assert "no changes since its newest version" in response.json()["detail"]
        assert "pipeline_errors" not in response.json()


@pytest.mark.django_db()
class TestBody:
    def test_an_unrecognised_key_is_a_400_naming_it(self, client, chatbot, dispatch):
        """Dropping it silently would publish -- possibly live -- on a body the client got wrong."""
        response = publish(client, chatbot, make_defualt=True)

        assert response.status_code == 400
        assert "make_defualt" in response.json()
        dispatch.assert_not_called()

    @pytest.mark.parametrize(
        "sent",
        [
            pytest.param({}, id="omitted"),
            pytest.param({"version_description": ""}, id="blank"),
            pytest.param({"version_description": None}, id="null"),
        ],
    )
    def test_an_absent_description_is_stored_as_blank(self, client, chatbot, dispatch, sent):
        """`version_description` is a non-null TextField, so the one representation of "unlabelled"
        is "" -- and a client echoing an inspect response back at us is not refused."""
        assert publish(client, chatbot, **sent).status_code == 202

        assert dispatch.call_args.kwargs["kwargs"]["version_description"] == ""


@pytest.mark.django_db()
def test_an_archived_chatbot_cannot_be_published(client, chatbot, dispatch):
    """Archived rows are outside every v2 write path's queryset, so this is a 404 rather than the
    UI's PermissionDenied."""
    chatbot.archive()

    assert publish(client, chatbot, make_default=True).status_code == 404
    dispatch.assert_not_called()


@pytest.mark.django_db()
def test_a_version_snapshot_cannot_itself_be_published(client, chatbot, dispatch):
    """Snapshots are immutable: only the working version is ever the subject of a write."""
    chatbot.create_new_version()

    assert publish(client, chatbot.versions.get(), make_default=True).status_code == 404
    dispatch.assert_not_called()
