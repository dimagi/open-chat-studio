"""Behaviour of the dashboard queryset builder after its move into
usage_metrics. The bulk of its behaviour is pinned by the existing
apps/dashboard test suites (which now exercise it through delegation) and by
test_characterisation.py; this file covers what the move adds - the same-team
constraint on tag links."""

from datetime import UTC, datetime

import pytest
import time_machine

from apps.chat.models import ChatMessage, ChatMessageType
from apps.usage_metrics.dashboard_querysets import filtered_querysets
from apps.utils.factories.annotations import CustomTaggedItemFactory, TagFactory
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
    session = ExperimentSessionFactory.create(team=team, experiment=experiment)
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
