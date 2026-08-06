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

    def test_cross_team_tag_link_does_not_qualify_a_local_chat(self):
        """The inconsistent-link shape: a CustomTaggedItem row carrying a
        FOREIGN team_id, whose tag is a local tag and whose object_id targets
        a local chat. The link is not the reading team's, so it must not make
        the session match."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = _active_session(team, experiment)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=foreign_team, tag=tag, target=session.chat)

        querysets = filtered_querysets(team, start_date=_START, end_date=_END, tag_ids=[tag.id])

        assert list(querysets["sessions"]) == []
