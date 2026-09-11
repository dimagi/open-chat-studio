import pytest
from django.urls import reverse

from apps.chat.models import ChatMessageType
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.fixture()
def session_with_messages(experiment):
    """25 messages, giving a clean 3-page split at page_size=10 (page 3 has 5, above orphans=3)."""
    session = ExperimentSessionFactory.create(
        experiment=experiment, participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner)
    )
    for i in range(25):
        ChatMessageFactory.create(
            chat=session.chat,
            message_type=ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI,
            content=f"Message {i}",
        )
    return session


def _messages_url(experiment, session):
    return reverse(
        "experiments:experiment_session_messages_view",
        kwargs={
            "team_slug": experiment.team.slug,
            "experiment_id": experiment.public_id,
            "session_id": session.external_id,
        },
    )


def _fragment_url(experiment, session):
    return reverse(
        "experiments:experiment_session_messages_fragment_view",
        kwargs={
            "team_slug": experiment.team.slug,
            "experiment_id": experiment.public_id,
            "session_id": session.external_id,
        },
    )


@pytest.mark.django_db()
class TestSessionMessagesLoadOnScroll:
    def test_first_page_renders_the_full_page_with_a_next_page_trigger(self, client, experiment, session_with_messages):
        client.force_login(experiment.owner)
        response = client.get(_messages_url(experiment, session_with_messages))

        assert response.status_code == 200
        assert len(response.context["messages"]) == 10
        assert response.context["page"] == 1
        assert response.context["total_pages"] == 3
        # Full page: control panel and wrapper are present.
        assert b'id="table-top"' in response.content
        assert b'id="control-panel"' in response.content
        # A trigger for page 2 is present, pointed at the dedicated fragment endpoint.
        assert _fragment_url(experiment, session_with_messages).encode() in response.content
        assert b"page=2" in response.content

    def test_next_page_request_renders_only_the_message_fragment(self, client, experiment, session_with_messages):
        client.force_login(experiment.owner)
        url = _fragment_url(experiment, session_with_messages) + "?page=2"
        response = client.get(url)

        assert response.status_code == 200
        assert len(response.context["messages"]) == 10
        assert response.context["page"] == 2
        # Fragment: no control panel or page wrapper duplicated in.
        assert b'id="table-top"' not in response.content
        assert b'id="control-panel"' not in response.content
        # Still on a middle page, so the next trigger (for page 3) is present.
        assert b"page=3" in response.content

    def test_last_page_has_no_further_trigger(self, client, experiment, session_with_messages):
        client.force_login(experiment.owner)
        url = _fragment_url(experiment, session_with_messages) + "?page=3"
        response = client.get(url)

        assert response.status_code == 200
        assert len(response.context["messages"]) == 5
        assert response.context["page"] == 3
        assert response.context["total_pages"] == 3
        assert _fragment_url(experiment, session_with_messages).encode() not in response.content

    def test_show_all_has_no_trigger_either(self, client, experiment, session_with_messages):
        client.force_login(experiment.owner)
        response = client.get(_messages_url(experiment, session_with_messages) + "?show_all=on")

        assert response.status_code == 200
        assert len(response.context["messages"]) == 25
        assert response.context["total_pages"] == 1
        assert _fragment_url(experiment, session_with_messages).encode() not in response.content

    def test_next_page_trigger_carries_the_search_filter_forward(self, client, experiment, session_with_messages):
        client.force_login(experiment.owner)
        response = client.get(_messages_url(experiment, session_with_messages) + "?search=Message&page=1")

        assert response.status_code == 200
        assert b"search=Message" in response.content

    def test_fragment_request_skips_the_control_panel_context(self, client, experiment, session_with_messages):
        """A fragment request doesn't render the control panel, so it shouldn't build the
        translation forms or query providers/models for it either."""
        client.force_login(experiment.owner)
        url = _fragment_url(experiment, session_with_messages) + "?page=2"
        response = client.get(url)

        assert response.status_code == 200
        for key in (
            "all_tags",
            "available_languages",
            "translate_form_all",
            "translate_form_remaining",
            "default_translation_models_by_providers",
            "llm_provider_models_dict",
        ):
            assert key not in response.context, key

    def test_full_page_request_still_has_the_control_panel_context(self, client, experiment, session_with_messages):
        client.force_login(experiment.owner)
        response = client.get(_messages_url(experiment, session_with_messages))

        assert response.status_code == 200
        for key in (
            "all_tags",
            "available_languages",
            "translate_form_all",
            "translate_form_remaining",
            "default_translation_models_by_providers",
            "llm_provider_models_dict",
        ):
            assert key in response.context, key

    def test_comment_row_toggle_uses_message_id_not_position_in_page(self, client, experiment, session_with_messages):
        """Each loaded page restarts its own forloop.counter, so a per-page-relative index would
        collide across pages once load-on-scroll keeps every page's messages in the DOM at once.
        message.id is unique across the whole session, so it can't collide this way."""
        client.force_login(experiment.owner)
        page_1 = client.get(_messages_url(experiment, session_with_messages))
        page_2 = client.get(_fragment_url(experiment, session_with_messages) + "?page=2")

        first_message_id_page_1 = page_1.context["messages"][0].id
        first_message_id_page_2 = page_2.context["messages"][0].id
        assert first_message_id_page_1 != first_message_id_page_2

        marker_1 = f"commentsRow === {first_message_id_page_1}".encode()
        marker_2 = f"commentsRow === {first_message_id_page_2}".encode()
        assert marker_1 in page_1.content
        assert marker_2 in page_2.content
