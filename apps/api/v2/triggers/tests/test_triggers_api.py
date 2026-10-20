"""The chatbot trigger endpoints: static, timeout and scheduled triggers (#4144)."""

import datetime

import pytest

from apps.events.models import EventActionType, StaticTrigger, TimeoutTrigger
from apps.teams.backends import (
    CHAT_VIEWER_GROUP,
    CHATBOT_ADMIN_GROUP,
    EVENT_ADMIN_GROUP,
    add_user_to_team,
    create_default_groups,
)
from apps.utils.factories.events import EventActionFactory
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

from .conftest import KINDS, LOG_ACTION, SCHEDULED, STATIC, TIMEOUT

SCHEDULE_PARAMS = {
    "name": "Weekly check-in",
    "prompt_text": "Ask how the week went.",
    "frequency": 1,
    "time_period": "weeks",
    "repetitions": 4,
}


def _error(response, *path):
    """The error message list at ``path`` in a 400 body."""
    body = response.json()
    for key in path:
        body = body[key]
    return body


# ── Create ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
@pytest.mark.parametrize("kind", KINDS)
def test_create_saves_a_working_trigger(client, chatbot, kind):
    response = client.post(kind.list_url(chatbot), kind.body(), format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    trigger = kind.model.objects.get(id=body["id"])
    assert trigger.experiment == chatbot
    assert trigger.working_version is None
    assert trigger.action.action_type == "log"
    assert body["is_active"] is True
    assert body["action"] == LOG_ACTION
    for field, value in kind.fields.items():
        assert body[field] == value


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("action", "stored_params"),
    [
        pytest.param({"action_type": "log", "params": {}}, {}, id="log"),
        pytest.param({"action_type": "end_conversation", "params": {}}, {}, id="end_conversation"),
        pytest.param(
            {"action_type": "send_message_to_bot", "params": {"message_to_bot": "Check in."}},
            {"message_to_bot": "Check in."},
            id="send_message_to_bot",
        ),
        pytest.param(
            {"action_type": "send_message_to_bot", "params": {}},
            {"message_to_bot": "The user hasn't responded, please prompt them again."},
            id="send_message_to_bot-default-message",
        ),
        pytest.param(
            {"action_type": "schedule_trigger", "params": SCHEDULE_PARAMS},
            SCHEDULE_PARAMS,
            id="schedule_trigger",
        ),
    ],
)
def test_action_params_contract(client, chatbot, action, stored_params):
    response = client.post(TIMEOUT.list_url(chatbot), TIMEOUT.body(action=action), format="json")

    assert response.status_code == 201, response.content
    assert response.json()["action"] == {"action_type": action["action_type"], "params": stored_params}
    stored = TimeoutTrigger.objects.get(id=response.json()["id"]).action.params
    # The scheduled message form records which chatbot runs the message; it is filled in, not sent.
    if action["action_type"] == "schedule_trigger":
        assert stored.pop("experiment_id") == str(chatbot.id)
    assert stored == stored_params


@pytest.mark.django_db()
def test_pipeline_start_accepts_a_standalone_pipeline(client, chatbot):
    pipeline = PipelineFactory.create(team=chatbot.team)
    action = {"action_type": "pipeline_start", "params": {"pipeline_id": pipeline.id, "input_type": "last_message"}}

    response = client.post(STATIC.list_url(chatbot), STATIC.body(action=action), format="json")

    assert response.status_code == 201, response.content
    assert response.json()["action"]["params"] == {"pipeline_id": pipeline.id, "input_type": "last_message"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "pipeline_of",
    [
        pytest.param(lambda chatbot: chatbot.pipeline, id="the-chatbots-own-pipeline"),
        pytest.param(lambda chatbot: PipelineFactory.create(), id="another-teams-pipeline"),
    ],
)
def test_pipeline_start_refuses_a_pipeline_outside_the_forms_choices(client, chatbot, pipeline_of):
    pipeline_id = pipeline_of(chatbot).id
    action = {"action_type": "pipeline_start", "params": {"pipeline_id": pipeline_id, "input_type": "last_message"}}

    response = client.post(STATIC.list_url(chatbot), STATIC.body(action=action), format="json")

    assert response.status_code == 400
    assert _error(response, "action", "params", "pipeline_id")
    assert not StaticTrigger.objects.filter(experiment=chatbot).exists()


@pytest.mark.django_db()
def test_schedule_trigger_refuses_a_client_supplied_experiment_id(client, chatbot):
    action = {"action_type": "schedule_trigger", "params": {**SCHEDULE_PARAMS, "experiment_id": chatbot.id}}

    response = client.post(TIMEOUT.list_url(chatbot), TIMEOUT.body(action=action), format="json")

    assert response.status_code == 400
    assert _error(response, "action", "params", "experiment_id")


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("trigger_type", "action_type"),
    [
        pytest.param("new_bot_message", "send_message_to_bot", id="new-bot-message-then-message-the-bot"),
        pytest.param("new_bot_message", "schedule_trigger", id="new-bot-message-then-schedule"),
        pytest.param("conversation_end", "end_conversation", id="conversation-end-then-end-it"),
    ],
)
def test_static_trigger_refuses_an_action_that_cannot_follow_it(client, chatbot, trigger_type, action_type):
    params = SCHEDULE_PARAMS if action_type == "schedule_trigger" else {}
    body = STATIC.body(type=trigger_type, action={"action_type": action_type, "params": params})

    response = client.post(STATIC.list_url(chatbot), body, format="json")

    assert response.status_code == 400
    assert _error(response, "action", "non_field_errors")


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("body", "error_path"),
    [
        pytest.param(TIMEOUT.body(colour="red"), ("colour",), id="unknown-trigger-key"),
        pytest.param(TIMEOUT.body(action={**LOG_ACTION, "extra": 1}), ("action", "extra"), id="unknown-action-key"),
        pytest.param(
            TIMEOUT.body(action={"action_type": "log", "params": {"delay": 5}}),
            ("action", "params", "delay"),
            id="params-key-the-action-type-does-not-take",
        ),
        pytest.param(
            TIMEOUT.body(action={"action_type": "frobnicate", "params": {}}),
            ("action", "action_type"),
            id="unknown-action-type",
        ),
        pytest.param(
            TIMEOUT.body(action={"action_type": ["log"], "params": {}}),
            ("action", "action_type"),
            id="action-type-not-a-string",
        ),
        pytest.param({"total_num_triggers": 1, "action": LOG_ACTION}, ("delay",), id="missing-delay"),
        pytest.param(TIMEOUT.body(delay="soon"), ("delay",), id="delay-not-an-integer"),
        pytest.param(TIMEOUT.body(total_num_triggers=0), ("total_num_triggers",), id="zero-trigger-count"),
        pytest.param(TIMEOUT.body(total_num_triggers=-5), ("total_num_triggers",), id="negative-trigger-count"),
        pytest.param(
            TIMEOUT.body(action={"action_type": "log", "params": []}),
            ("action", "params"),
            id="params-not-an-object",
        ),
        pytest.param({**TIMEOUT.fields, "action": None}, ("action",), id="null-action"),
        pytest.param({"delay": 60}, ("action",), id="missing-action"),
        pytest.param(
            TIMEOUT.body(action={"action_type": "schedule_trigger", "params": {"name": "x"}}),
            ("action", "params", "prompt_text"),
            id="schedule-missing-params",
        ),
    ],
)
def test_create_refuses_a_bad_body(client, chatbot, body, error_path):
    response = client.post(TIMEOUT.list_url(chatbot), body, format="json")

    assert response.status_code == 400, response.content
    assert _error(response, *error_path)
    assert not TimeoutTrigger.objects.filter(experiment=chatbot).exists()


@pytest.mark.django_db()
def test_scheduled_trigger_refuses_a_time_in_the_past(client, chatbot):
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    response = client.post(SCHEDULED.list_url(chatbot), SCHEDULED.body(trigger_date=yesterday), format="json")

    assert response.status_code == 400
    assert _error(response, "trigger_date")


@pytest.mark.django_db()
def test_scheduled_trigger_reports_when_it_fires(client, chatbot):
    response = client.post(SCHEDULED.list_url(chatbot), SCHEDULED.body(timezone="UTC"), format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["scheduled_at"].startswith(f"{SCHEDULED.fields['trigger_date']}T09:30:00")
    assert body["fired_at"] is None


# ── Update ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_patch_changes_only_the_fields_sent(client, chatbot):
    trigger = TIMEOUT.factory.create(
        experiment=chatbot, delay=60, total_num_triggers=3, trigger_from_first_message=True
    )

    response = client.patch(TIMEOUT.detail_url(chatbot, trigger.id), {"delay": 120}, format="json")

    assert response.status_code == 200, response.content
    trigger.refresh_from_db()
    assert (trigger.delay, trigger.total_num_triggers, trigger.trigger_from_first_message) == (120, 3, True)
    assert trigger.action.action_type == "log"


@pytest.fixture()
def scheduling_timeout(chatbot):
    """A timeout trigger whose action schedules messages, stored as the web app stores it."""
    action = EventActionFactory.create(
        action_type=EventActionType.SCHEDULETRIGGER, params={**SCHEDULE_PARAMS, "experiment_id": str(chatbot.id)}
    )
    return TIMEOUT.factory.create(experiment=chatbot, action=action)


@pytest.mark.django_db()
def test_patch_without_an_action_keeps_the_actions_params(client, chatbot, scheduling_timeout):
    response = client.patch(TIMEOUT.detail_url(chatbot, scheduling_timeout.id), {"delay": 120}, format="json")

    assert response.status_code == 200, response.content
    scheduling_timeout.action.refresh_from_db()
    assert scheduling_timeout.action.params == {**SCHEDULE_PARAMS, "experiment_id": str(chatbot.id)}


@pytest.mark.django_db()
def test_patch_replaces_the_params_rather_than_merging_them(client, chatbot, scheduling_timeout):
    response = client.patch(
        TIMEOUT.detail_url(chatbot, scheduling_timeout.id), {"action": {"params": {"frequency": 2}}}, format="json"
    )

    assert response.status_code == 400
    assert _error(response, "action", "params", "prompt_text")
    scheduling_timeout.action.refresh_from_db()
    assert scheduling_timeout.action.params["frequency"] == 1


@pytest.mark.django_db()
def test_patch_keeps_the_action_type_when_it_is_not_sent(client, chatbot):
    action = EventActionFactory.create(action_type="send_message_to_bot", params={"message_to_bot": "Old"})
    trigger = TIMEOUT.factory.create(experiment=chatbot, action=action)

    response = client.patch(
        TIMEOUT.detail_url(chatbot, trigger.id), {"action": {"params": {"message_to_bot": "New"}}}, format="json"
    )

    assert response.status_code == 200, response.content
    assert response.json()["action"] == {"action_type": "send_message_to_bot", "params": {"message_to_bot": "New"}}


@pytest.mark.django_db()
def test_patch_changes_the_action_type(client, chatbot):
    trigger = STATIC.factory.create(experiment=chatbot)
    action = {"action_type": "send_message_to_bot", "params": {"message_to_bot": "Hello"}}

    response = client.patch(STATIC.detail_url(chatbot, trigger.id), {"action": action}, format="json")

    assert response.status_code == 200, response.content
    trigger.refresh_from_db()
    assert (trigger.action.action_type, trigger.action.params) == ("send_message_to_bot", {"message_to_bot": "Hello"})


@pytest.mark.django_db()
def test_patch_checks_a_new_static_type_against_the_existing_action(client, chatbot):
    action = EventActionFactory.create(action_type="send_message_to_bot", params={"message_to_bot": "Hi"})
    trigger = STATIC.factory.create(experiment=chatbot, action=action, type="conversation_start")

    response = client.patch(STATIC.detail_url(chatbot, trigger.id), {"type": "new_bot_message"}, format="json")

    assert response.status_code == 400
    assert _error(response, "action", "non_field_errors")
    trigger.refresh_from_db()
    assert trigger.type == "conversation_start"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("kind", "body"),
    [
        pytest.param(TIMEOUT, {"is_active": False}, id="is-active"),
        pytest.param(TIMEOUT, {"id": 0}, id="id"),
        pytest.param(SCHEDULED, {"fired_at": "2020-01-01T00:00:00Z"}, id="fired-at"),
    ],
)
def test_patch_refuses_a_change_to_a_read_only_field(client, chatbot, kind, body):
    trigger = kind.factory.create(experiment=chatbot)

    response = client.patch(kind.detail_url(chatbot, trigger.id), body, format="json")

    assert response.status_code == 400, response.content
    (key,) = body
    assert "Read-only field" in str(_error(response, key))
    trigger.refresh_from_db()
    assert trigger.is_active is True


@pytest.mark.django_db()
def test_create_refuses_a_read_only_field(client, chatbot):
    response = client.post(TIMEOUT.list_url(chatbot), TIMEOUT.body(is_active=True), format="json")

    assert response.status_code == 400, response.content
    assert "Read-only field" in str(_error(response, "is_active"))
    assert not TimeoutTrigger.objects.filter(experiment=chatbot).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize("kind", KINDS)
def test_the_response_can_be_sent_back_as_a_patch(client, chatbot, kind):
    created = client.post(kind.list_url(chatbot), kind.body(), format="json").json()

    response = client.patch(kind.detail_url(chatbot, created["id"]), created, format="json")

    assert response.status_code == 200, response.content
    assert response.json() == created


# ── Archive ──────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
@pytest.mark.parametrize("kind", KINDS)
def test_delete_archives_the_trigger(client, chatbot, kind):
    trigger = kind.factory.create(experiment=chatbot)

    response = client.delete(kind.detail_url(chatbot, trigger.id))

    assert response.status_code == 200, response.content
    assert response.json() == {"archived": True}
    assert kind.model.objects.get_all().get(id=trigger.id).is_archived is True
    assert client.delete(kind.detail_url(chatbot, trigger.id)).status_code == 404


# ── Which triggers can be addressed ──────────────────────────────────────────────────────────────
@pytest.mark.django_db()
@pytest.mark.parametrize("kind", KINDS)
def test_a_published_versions_trigger_is_not_addressable(client, chatbot, kind):
    kind.factory.create(experiment=chatbot)
    version = chatbot.create_new_version()
    published_trigger = getattr(version, kind.related_name).get()

    response = client.patch(kind.detail_url(chatbot, published_trigger.id), {}, format="json")

    assert response.status_code == 404


@pytest.mark.django_db()
@pytest.mark.parametrize("kind", KINDS)
def test_another_chatbots_trigger_is_not_addressable(client, chatbot, kind):
    other = kind.factory.create(experiment=ChatbotFactory.create(team=chatbot.team))

    assert client.patch(kind.detail_url(chatbot, other.id), {}, format="json").status_code == 404
    assert client.delete(kind.detail_url(chatbot, other.id)).status_code == 404


@pytest.mark.django_db()
def test_a_trigger_is_addressed_under_its_own_kind(client, chatbot):
    timeout = TIMEOUT.factory.create(experiment=chatbot)

    assert client.delete(STATIC.detail_url(chatbot, timeout.id)).status_code == 404


@pytest.mark.django_db()
def test_another_teams_chatbot_is_not_found(client):
    other_chatbot = ChatbotFactory.create()

    response = client.post(TIMEOUT.list_url(other_chatbot), TIMEOUT.body(), format="json")

    assert response.status_code == 404


# ── Auth ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_a_read_only_key_cannot_write(chatbot):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, read_only=True)

    assert client.post(TIMEOUT.list_url(chatbot), TIMEOUT.body(), format="json").status_code == 403


@pytest.mark.django_db()
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method", ["post", "patch", "delete"])
@pytest.mark.parametrize(
    ("group", "allowed"),
    [
        pytest.param(EVENT_ADMIN_GROUP, True, id="event_admin"),
        pytest.param(CHATBOT_ADMIN_GROUP, False, id="chatbot_admin"),
        pytest.param(CHAT_VIEWER_GROUP, False, id="chat_viewer"),
    ],
)
def test_writes_require_the_events_permissions_the_web_views_require(chatbot, kind, method, group, allowed):
    create_default_groups()
    user = UserFactory.create()
    add_user_to_team(chatbot.team, user, [group])
    client = ApiTestClient(user, chatbot.team)
    trigger = kind.factory.create(experiment=chatbot)

    if method == "post":
        response = client.post(kind.list_url(chatbot), kind.body(), format="json")
    else:
        response = getattr(client, method)(kind.detail_url(chatbot, trigger.id), {}, format="json")

    assert (response.status_code != 403) is allowed, response.content


@pytest.mark.django_db()
def test_a_token_with_only_the_read_scope_cannot_write(chatbot):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, auth_method="oauth", scopes=["chatbots:read"])

    assert client.post(TIMEOUT.list_url(chatbot), TIMEOUT.body(), format="json").status_code == 403


@pytest.mark.django_db()
def test_options_describes_the_patch_body(client, chatbot):
    trigger = TIMEOUT.factory.create(experiment=chatbot)

    response = client.options(TIMEOUT.detail_url(chatbot, trigger.id))

    assert response.status_code == 200
    assert "delay" in response.json()["actions"]["PATCH"]


@pytest.mark.django_db()
def test_a_machine_token_reaches_only_its_allowed_chatbots(chatbot):
    client = ApiTestClient(
        UserFactory.create(),
        chatbot.team,
        auth_method="oauth_client_credentials",
        scopes=["chatbots:write"],
        allowed_chatbots=[],
    )

    response = client.post(TIMEOUT.list_url(chatbot), TIMEOUT.body(), format="json")

    assert response.status_code == 403
    assert not TimeoutTrigger.objects.filter(experiment=chatbot).exists()


# ── Read back through inspect ────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_the_24h_timeout_from_the_spec_reads_back_through_inspect(client, chatbot):
    body = {
        "delay": 86400,
        "total_num_triggers": 1,
        "action": {
            "action_type": "send_message_to_bot",
            "params": {"message_to_bot": "The participant has been inactive for 24 hours. Send a gentle check-in."},
        },
    }
    created = client.post(TIMEOUT.list_url(chatbot), body, format="json").json()

    inspected = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/").json()

    assert inspected["events"]["timeout_triggers"] == [
        {
            "id": created["id"],
            "delay": 86400,
            "total_num_triggers": 1,
            "trigger_from_first_message": False,
            "is_active": True,
            "action": {"action_type": "send_message_to_bot", "params": body["action"]["params"]},
        }
    ]


@pytest.mark.django_db()
def test_a_scheduled_trigger_reads_back_through_inspect(client, chatbot):
    created = client.post(SCHEDULED.list_url(chatbot), SCHEDULED.body(), format="json").json()

    inspected = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/").json()

    assert inspected["events"]["scheduled_triggers"] == [
        {
            "id": created["id"],
            "trigger_date": SCHEDULED.fields["trigger_date"],
            "trigger_time": "09:30:00",
            "timezone": "Africa/Johannesburg",
            "active_session_window_days": None,
            "scheduled_at": created["scheduled_at"],
            "fired_at": None,
            "is_active": True,
            "action": {"action_type": "log", "params": {}},
        }
    ]


@pytest.mark.django_db()
@pytest.mark.parametrize("kind", KINDS)
def test_inspect_shows_a_trigger_as_the_write_endpoint_returns_it(client, chatbot, kind):
    created = client.post(kind.list_url(chatbot), kind.body(), format="json").json()

    inspected = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/").json()

    assert inspected["events"][kind.related_name] == [created]
