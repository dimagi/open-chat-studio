"""Behaviour of the dashboard queryset builder after its move into
usage_metrics. The bulk of its behaviour is pinned by the existing
apps/dashboard test suites (which now exercise it through delegation) and by
test_characterisation.py; this file covers what the move adds - the same-team
constraint on tag links."""

from datetime import UTC, datetime

import pytest
import time_machine
from field_audit.models import AuditAction

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.usage_metrics.dashboard_querysets import filtered_querysets
from apps.utils.factories.annotations import CustomTaggedItemFactory, TagFactory
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

_START = datetime(2026, 6, 1, tzinfo=UTC)
_END = datetime(2026, 6, 15, tzinfo=UTC)
_MID = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db()


@pytest.fixture(autouse=True)
def _frozen_time():
    with time_machine.travel(_MID, tick=False):
        yield


def _active_session(team, experiment):
    # experiment_channel is pinned to `experiment` too: left at its default, ExperimentChannelFactory's
    # own SubFactory builds an unrelated Experiment for the same team, which would show up as a second,
    # non-archived experiment and defeat any assertion that counts a team's experiments directly (see the
    # same fix in test_characterisation.py's TestArchivedExperimentActivity._team).
    session = ExperimentSessionFactory.create(
        team=team,
        experiment=experiment,
        status=SessionStatus.ACTIVE,
        experiment_channel=ExperimentChannelFactory(team=team, experiment=experiment),
    )
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="x", created_at=_MID)
    return session


class TestTagFilterTeamScoping:
    def test_own_teams_tag_link_matches(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=session.chat)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert [s.id for s in querysets["sessions"]] == [session.id]
        assert [e.id for e in querysets["experiments"]] == [experiment.id]
        assert [p.id for p in querysets["participants"]] == [session.participant_id]

    def test_cross_team_tag_link_does_not_qualify_a_local_chat(self):
        """The inconsistent-link shape: a CustomTaggedItem row carrying a
        FOREIGN team_id, whose tag is a local tag and whose object_id targets
        a local chat. The link is not the reading team's, so it must not make
        the session, experiment, or participant match."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=foreign_team, tag=tag, target=session.chat)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert list(querysets["sessions"]) == []
        assert list(querysets["experiments"]) == []
        assert list(querysets["participants"]) == []

    def test_locally_recorded_link_with_foreign_team_tag_does_not_qualify_a_chat(self):
        """The mirror inconsistent-link shape: a CustomTaggedItem row with a
        LOCAL team_id, whose tag belongs to a FOREIGN team, targeting a local
        chat. The tag isn't the reading team's, so it must not make the
        session, experiment, or participant match."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        foreign_tag = TagFactory.create(team=foreign_team)
        CustomTaggedItemFactory.create(team=team, tag=foreign_tag, target=session.chat)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[foreign_tag.id])

        assert list(querysets["sessions"]) == []
        assert list(querysets["experiments"]) == []
        assert list(querysets["participants"]) == []

    def test_locally_recorded_link_with_foreign_team_tag_does_not_qualify_a_message(self):
        """Same mirror shape as above, but the link targets a MESSAGE rather
        than the chat - exercises the `_on_msg` predicates (`tag_on_msg`,
        `exp_tag_on_msg`, `part_tag_on_msg`), which a chat-targeted tag never
        reaches."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        foreign_tag = TagFactory.create(team=foreign_team)
        CustomTaggedItemFactory.create(team=team, tag=foreign_tag, target=message)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[foreign_tag.id])

        assert list(querysets["sessions"]) == []
        assert list(querysets["experiments"]) == []
        assert list(querysets["participants"]) == []

    def test_foreign_team_link_with_local_tag_does_not_qualify_via_a_message(self):
        """The remaining inconsistent-link shape: a CustomTaggedItem row with
        a FOREIGN team_id, whose tag IS local, attached to a MESSAGE rather
        than the chat. A chat-targeted foreign-team_id link (see
        `test_cross_team_tag_link_does_not_qualify_a_local_chat`) never
        reaches `tag_on_msg`/`exp_tag_on_msg`/`part_tag_on_msg`, since those
        subqueries filter on `content_type=message_content_type` - this is
        the test that does."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=foreign_team, tag=tag, target=message)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert list(querysets["sessions"]) == []
        assert list(querysets["experiments"]) == []
        assert list(querysets["participants"]) == []

    def test_own_team_link_on_a_message_matches(self):
        """Positive control for the test above: the same message-targeted
        link, but with a LOCAL team_id, must still match - otherwise the
        negative assertion above proves nothing."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=message)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert [s.id for s in querysets["sessions"]] == [session.id]
        assert [e.id for e in querysets["experiments"]] == [experiment.id]
        assert [p.id for p in querysets["participants"]] == [session.participant_id]


class TestMessageTagFilterTeamScoping:
    """The 9th tag-link site: the `messages` leg used to use a plain
    `tags__id__in` M2M filter, which - unlike the other seven tag sites in
    this module - carried neither the `team_id` nor the `tag__team_id`
    predicate. It now uses a team-scoped `Exists` against `CustomTaggedItem`
    for the message's own tags, matching the other sites' shape."""

    def test_cross_team_link_with_local_tag_on_a_local_message_does_not_qualify(self):
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=foreign_team, tag=tag, target=message)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert list(querysets["messages"]) == []

    def test_local_link_with_foreign_team_tag_on_a_local_message_does_not_qualify(self):
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        foreign_tag = TagFactory.create(team=foreign_team)
        CustomTaggedItemFactory.create(team=team, tag=foreign_tag, target=message)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[foreign_tag.id])

        assert list(querysets["messages"]) == []

    def test_own_teams_link_on_a_message_matches(self):
        """Positive control: the reading team's own link on its own tag,
        targeting a message, must qualify - otherwise the two negative
        assertions above prove nothing."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=message)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert [m.id for m in querysets["messages"]] == [message.id]

    def test_tag_on_the_chat_does_not_pull_its_messages_into_the_messages_leg(self):
        """Message-only semantics control: the sessions/experiments/
        participants legs use the broader chat-or-message match
        (`chat_tag_exists_pair`), but the messages leg must not - a tag
        recorded on the chat rather than on a message must not make that
        chat's messages match here. Widening to chat-or-message would be an
        unrequested behaviour change."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="untagged", created_at=_MID
        )
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=session.chat)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert list(querysets["messages"]) == []
        # sanity: the chat-level tag does still qualify the session, so the empty messages
        # result above is the message-only leg behaving correctly, not a broken fixture.
        assert [s.id for s in querysets["sessions"]] == [session.id]


class TestEvaluationExclusionColumn:
    """`ExperimentSession.platform` is the sole discriminator for
    evaluation-harness activity (ADR-0051). The session's channel may carry a
    different platform - the two are independent nullable columns - and the
    channel's value must not decide the session's fate either way."""

    def test_session_platform_decides_exclusion_not_the_channels(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        # Session is an evaluation session; its channel says otherwise.
        eval_session = _active_session(team, experiment)
        ExperimentSession.objects.filter(pk=eval_session.pk).update(platform=ChannelPlatform.EVALUATIONS)
        # Session is a real web session; its channel says evaluations.
        web_session = _active_session(team, experiment)
        ExperimentSession.objects.filter(pk=web_session.pk).update(platform=ChannelPlatform.WEB)
        ExperimentChannel.objects.filter(pk=web_session.experiment_channel_id).update(
            platform=ChannelPlatform.EVALUATIONS, audit_action=AuditAction.IGNORE
        )

        querysets = filtered_querysets(team, start_date=_START, end_date=_END)

        assert [s.id for s in querysets["sessions"]] == [web_session.id]


class TestIncludeArchived:
    """`include_archived` applies to experiment enumeration only. Activity
    metrics count archived-chatbot activity either way - it happened, and the
    spend was real (ADR-0051)."""

    def _team(self):
        team = TeamFactory.create()
        live = ExperimentFactory.create(team=team)
        _active_session(team, live)
        archived = ExperimentFactory.create(team=team, is_archived=True)
        _active_session(team, archived)
        return team

    def test_enumeration_excludes_archived_by_default(self):
        querysets = filtered_querysets(self._team(), start_date=_START, end_date=_END)

        assert querysets["experiments"].count() == 1

    def test_enumeration_includes_archived_when_asked(self):
        querysets = filtered_querysets(self._team(), start_date=_START, end_date=_END, include_archived=True)

        assert querysets["experiments"].count() == 2

    def test_activity_counts_archived_chatbots_either_way(self):
        team = self._team()

        default = filtered_querysets(team, start_date=_START, end_date=_END)
        inclusive = filtered_querysets(team, start_date=_START, end_date=_END, include_archived=True)

        assert default["sessions"].count() == inclusive["sessions"].count() == 2
