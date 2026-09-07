import pytest
from django.urls import reverse

from apps.annotations.models import Tag, TagCategories
from apps.chat.models import ChatMessageType
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.traces import TraceFactory


@pytest.mark.django_db()
class TestParticipantDataDiffInSessionMessages:
    def test_messages_queryset_includes_participant_data_diff(self, client, experiment):
        session = ExperimentSessionFactory.create(
            experiment=experiment,
            participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner),
        )

        messages = []
        for i in range(5):
            msg_type = ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI
            messages.append(
                ChatMessageFactory.create(
                    chat=session.chat,
                    message_type=msg_type,
                    content=f"Message {i}",
                )
            )

        # Attach traces with participant_data_diff to the 2nd and 4th messages (AI messages)
        diff_1 = [["change", "plan", ["free", "pro"]]]
        diff_2 = [["add", "", [["score", 100]]], ["change", "level", [1, 2]]]
        TraceFactory.create(
            experiment=experiment,
            session=session,
            participant=session.participant,
            team=experiment.team,
            participant_data_diff=diff_1,
            output_message=messages[1],
        )
        TraceFactory.create(
            experiment=experiment,
            session=session,
            participant=session.participant,
            team=experiment.team,
            participant_data_diff=diff_2,
            output_message=messages[3],
        )

        client.force_login(experiment.owner)
        url = reverse(
            "experiments:experiment_session_messages_view",
            kwargs={
                "team_slug": experiment.team.slug,
                "experiment_id": experiment.public_id,
                "session_id": session.external_id,
            },
        )
        response = client.get(url + "?show_all=on")
        assert response.status_code == 200
        page_messages = response.context["messages"]

        assert len(page_messages) == 5
        assert page_messages[0].participant_data_diff_from_trace is None
        assert page_messages[1].participant_data_diff_from_trace == diff_1
        assert page_messages[2].participant_data_diff_from_trace is None
        assert page_messages[3].participant_data_diff_from_trace == diff_2
        assert page_messages[4].participant_data_diff_from_trace is None


@pytest.mark.django_db()
class TestTagFilterInSessionMessages:
    def test_selecting_multiple_tags_keeps_all_of_them(self, client, experiment):
        """The tag_filter checkboxes all share the same field name, so multiple selections arrive
        as repeated query params. Reading it with QueryDict.get() silently keeps only the last one.
        """
        session = ExperimentSessionFactory.create(
            experiment=experiment,
            participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner),
        )
        billing_tag = Tag.objects.create(name="billing", team=experiment.team, category=TagCategories.BOT_RESPONSE)
        urgent_tag = Tag.objects.create(name="urgent", team=experiment.team, category=TagCategories.BOT_RESPONSE)

        billing_message = ChatMessageFactory.create(
            chat=session.chat, message_type=ChatMessageType.AI, content="Billing message"
        )
        billing_message.add_tag(billing_tag, team=experiment.team, added_by=None)
        urgent_message = ChatMessageFactory.create(
            chat=session.chat, message_type=ChatMessageType.AI, content="Urgent message"
        )
        urgent_message.add_tag(urgent_tag, team=experiment.team, added_by=None)
        ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.AI, content="Untagged message")

        client.force_login(experiment.owner)
        url = reverse(
            "experiments:experiment_session_messages_view",
            kwargs={
                "team_slug": experiment.team.slug,
                "experiment_id": experiment.public_id,
                "session_id": session.external_id,
            },
        )
        response = client.get(f"{url}?show_all=on&tag_filter=billing&tag_filter=urgent")
        assert response.status_code == 200
        page_messages = response.context["messages"]

        assert {message.id for message in page_messages} == {billing_message.id, urgent_message.id}


@pytest.mark.django_db()
class TestPushUrlInSessionMessages:
    def test_htmx_request_pushes_the_full_session_page_url(self, client, experiment):
        """This view only ever renders the messages fragment. Letting htmx push its own URL
        would leave a refresh pointing at that bare fragment instead of the full page, so the
        view has to push the full page's URL (with the same filters) itself.
        """
        session = ExperimentSessionFactory.create(
            experiment=experiment,
            participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner),
        )
        client.force_login(experiment.owner)
        url = reverse(
            "experiments:experiment_session_messages_view",
            kwargs={
                "team_slug": experiment.team.slug,
                "experiment_id": experiment.public_id,
                "session_id": session.external_id,
            },
        )
        expected_page_url = reverse(
            "chatbots:chatbot_session_view",
            args=[experiment.team.slug, experiment.public_id, session.external_id],
        )

        response = client.get(f"{url}?tag_filter=billing", headers={"HX-Request": "true"})

        assert response["HX-Push-Url"] == f"{expected_page_url}?tag_filter=billing"

    def test_non_htmx_request_does_not_set_the_header(self, client, experiment):
        session = ExperimentSessionFactory.create(
            experiment=experiment,
            participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner),
        )
        client.force_login(experiment.owner)
        url = reverse(
            "experiments:experiment_session_messages_view",
            kwargs={
                "team_slug": experiment.team.slug,
                "experiment_id": experiment.public_id,
                "session_id": session.external_id,
            },
        )

        response = client.get(url)

        assert "HX-Push-Url" not in response
