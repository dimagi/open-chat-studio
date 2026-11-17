"""The dashboard's queryset adapter: the same-team constraint on tag links, the
evaluation discriminator, archived-chatbot enumeration, and the two meanings of
a platform filter. The session and message universes it hands out are
`metrics.py`'s, pinned in test_metrics.py."""

from datetime import UTC, datetime

import pytest
import time_machine
from field_audit.models import AuditAction

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.usage_metrics.dashboard_querysets import filtered_querysets
from apps.usage_metrics.filters import UsageFilters
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

        querysets = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

        assert [s.id for s in querysets["sessions"]] == [session.id]
        assert [e.id for e in querysets["experiments"]] == [experiment.id]
        assert [p.id for p in querysets["participants"]] == [session.participant_id]

    @pytest.mark.parametrize(
        ("failing_conjunct", "target"),
        [
            pytest.param("link-team", "chat", id="link-team-conjunct-on-chat"),
            pytest.param("tag-team", "chat", id="tag-team-conjunct-on-chat"),
            pytest.param("tag-team", "msg", id="tag-team-conjunct-on-msg"),
            pytest.param("link-team", "msg", id="link-team-conjunct-on-msg"),
        ],
    )
    def test_link_failing_one_scoping_conjunct_qualifies_nothing(self, failing_conjunct, target):
        """Every tag site scopes links with two conjuncts, `team_id` and
        `tag__team_id`, applied on a chat leg and a message leg
        (`chat_tag_exists_pair` for sessions, `tagged_conversation_exists_pair`
        for experiments and participants). Each case builds a link where
        exactly one conjunct fails on one leg while the other three
        predicates hold; the legs filter on their own content_type, so a
        chat-targeted link never reaches the `_on_msg` predicates and vice
        versa. The three assertions cover the three querysets that consume
        the predicates. Positive controls:
        `test_own_teams_tag_link_matches` / `test_own_team_link_on_a_message_matches`."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        link_team = foreign_team if failing_conjunct == "link-team" else team
        tag = TagFactory.create(team=foreign_team if failing_conjunct == "tag-team" else team)
        link_target = (
            ChatMessage.objects.create(
                chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
            )
            if target == "msg"
            else session.chat
        )
        CustomTaggedItemFactory.create(team=link_team, tag=tag, target=link_target)

        querysets = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

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

        querysets = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

        assert [s.id for s in querysets["sessions"]] == [session.id]
        assert [e.id for e in querysets["experiments"]] == [experiment.id]
        assert [p.id for p in querysets["participants"]] == [session.participant_id]


class TestMessageTagFilterTeamScoping:
    """The 9th tag-link site: the `messages` leg used to use a plain
    `tags__id__in` M2M filter, which - unlike the other seven tag sites in
    this module - carried neither the `team_id` nor the `tag__team_id`
    predicate. It now uses a team-scoped `Exists` against `CustomTaggedItem`
    for the message's own tags, matching the other sites' shape."""

    @pytest.mark.parametrize(
        "failing_conjunct",
        [
            pytest.param("link-team", id="link-team-conjunct"),
            pytest.param("tag-team", id="tag-team-conjunct"),
        ],
    )
    def test_link_failing_one_scoping_conjunct_does_not_qualify_a_message(self, failing_conjunct):
        """Each case builds a message-targeted link where exactly one of the
        two scoping conjuncts fails while the other holds. Positive control:
        `test_own_teams_link_on_a_message_matches`."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        link_team = foreign_team if failing_conjunct == "link-team" else team
        tag = TagFactory.create(team=foreign_team if failing_conjunct == "tag-team" else team)
        CustomTaggedItemFactory.create(team=link_team, tag=tag, target=message)

        querysets = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

        assert list(querysets["messages"]) == []

    def test_own_teams_link_on_a_message_matches(self):
        """Positive control: the reading team's own link on its own tag,
        targeting a message, must qualify - otherwise the two negative
        assertions above prove nothing. The match is chat-or-message, so the
        tagged message pulls its whole conversation in: the session's other
        (untagged) message qualifies alongside it."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        message = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.HUMAN, content="tagged", created_at=_MID
        )
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=message)

        querysets = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

        assert {m.id for m in querysets["messages"]} == set(
            ChatMessage.objects.filter(chat=session.chat).values_list("id", flat=True)
        )

    def test_tag_on_the_chat_pulls_its_messages_into_the_messages_leg(self):
        """Every leg matches tags chat-or-message (`chat_tag_exists_pair`): a
        tag recorded on the chat qualifies the conversation's messages the same
        way it qualifies the session. Without this, a chat-level tag filter
        counted the session on one card while zeroing the message and
        participant counts beside it, and the dashboard disagreed with
        `usage_metrics.messages` under the same filter."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        untagged_session = _active_session(team, experiment)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=session.chat)

        querysets = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

        assert {m.id for m in querysets["messages"]} == set(
            ChatMessage.objects.filter(chat=session.chat).values_list("id", flat=True)
        )
        assert [s.id for s in querysets["sessions"]] == [session.id]
        assert untagged_session.chat.messages.exists()  # the excluded shape is real, not an empty fixture


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

        querysets = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters())

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

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            pytest.param(UsageFilters(), 2, id="included-by-default"),
            pytest.param(UsageFilters(include_archived=False), 1, id="excluded-when-asked"),
        ],
    )
    def test_enumeration_honours_include_archived(self, filters, expected):
        querysets = filtered_querysets(self._team(), start=_START, end=_END, filters=filters)

        assert querysets["experiments"].count() == expected

    def test_activity_counts_archived_chatbots_either_way(self):
        team = self._team()

        inclusive = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters())
        excluding = filtered_querysets(team, start=_START, end=_END, filters=UsageFilters(include_archived=False))

        assert inclusive["sessions"].count() == excluding["sessions"].count() == 2


class TestPlatformFilter:
    """A platform pick means two things: a channel on that platform for a chatbot, and the session's
    own platform column for sessions, messages and participants. The two columns can disagree on a row."""

    def test_chatbots_match_on_a_channel_and_sessions_on_their_own_column(self):
        team = TeamFactory.create()
        telegram_bot = ExperimentFactory.create(team=team)
        ExperimentChannelFactory.create(team=team, experiment=telegram_bot, platform=ChannelPlatform.TELEGRAM)
        telegram_session = _active_session(team, telegram_bot)
        ExperimentSession.objects.filter(pk=telegram_session.pk).update(platform=ChannelPlatform.TELEGRAM)
        web_bot = ExperimentFactory.create(team=team)
        web_session = _active_session(team, web_bot)
        ExperimentSession.objects.filter(pk=web_session.pk).update(platform=ChannelPlatform.WEB)
        ExperimentChannel.objects.filter(pk=web_session.experiment_channel_id).update(
            platform=ChannelPlatform.WEB, audit_action=AuditAction.IGNORE
        )

        querysets = filtered_querysets(
            team, start=_START, end=_END, filters=UsageFilters(platforms=[ChannelPlatform.TELEGRAM])
        )

        assert [e.id for e in querysets["experiments"]] == [telegram_bot.id]
        assert [s.id for s in querysets["sessions"]] == [telegram_session.id]
        assert {m.chat_id for m in querysets["messages"]} == {telegram_session.chat_id}

    def test_a_team_global_platform_keeps_every_chatbot(self):
        """Every chatbot has the web and API platforms, so a pick of one does not narrow the list."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        ExperimentSession.objects.filter(pk=session.pk).update(platform=ChannelPlatform.WEB)

        querysets = filtered_querysets(
            team, start=_START, end=_END, filters=UsageFilters(platforms=[ChannelPlatform.WEB])
        )

        assert [e.id for e in querysets["experiments"]] == [experiment.id]
        assert [s.id for s in querysets["sessions"]] == [session.id]
