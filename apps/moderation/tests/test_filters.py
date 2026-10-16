"""Tests for the participant blocked filter on the session list and the participant table."""

from types import SimpleNamespace

import pytest
from django.http import QueryDict
from waffle.testutils import override_flag

from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession, Participant
from apps.participants.filters import ParticipantFilter
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.team import TeamFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


def _apply(*, filter_class, queryset, query_param, value, operator, flag_active=True):
    query_dict = QueryDict("", mutable=True)
    query_dict.update({f"f_{query_param}": value, f"op_{query_param}": operator})
    with override_flag(Flags.ABUSE_DETECTION.slug, active=flag_active):
        return filter_class().apply(queryset, filter_params=FilterParams(query_dict))


@pytest.fixture()
def sessions(db):
    """Sessions whose participant is blocked, not blocked, or blocked only on another team."""
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)
    blocked = ExperimentSessionFactory.create(experiment=experiment)
    DeniedParticipantFactory.create(team=team, participant=blocked.participant)
    # A second session for the blocked participant, so a JOIN-based filter would duplicate rows.
    blocked_again = ExperimentSessionFactory.create(experiment=experiment, participant=blocked.participant)
    not_blocked = ExperimentSessionFactory.create(experiment=experiment)
    other_team_block = ExperimentSessionFactory.create(experiment=experiment)
    # The denylist row names this participant but belongs to a different team, so it does not block them here.
    DeniedParticipantFactory.create(team=TeamFactory.create(), participant=other_team_block.participant)
    return SimpleNamespace(
        team=team,
        blocked=blocked,
        blocked_again=blocked_again,
        not_blocked=not_blocked,
        other_team_block=other_team_block,
    )


FILTER_TABLES = pytest.mark.parametrize(
    ("filter_class", "queryset_factory", "query_param", "row_id"),
    [
        pytest.param(
            ExperimentSessionFilter,
            lambda team: ExperimentSession.objects.filter(team=team),
            "participant_blocked",
            lambda session: session.id,
            id="sessions",
        ),
        pytest.param(
            ParticipantFilter,
            lambda team: Participant.objects.filter(team=team),
            "blocked",
            lambda session: session.participant_id,
            id="participants",
        ),
    ],
)


@FILTER_TABLES
@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param(Operators.ANY_OF, "true", ["blocked", "blocked_again"], id="any-of-blocked"),
        pytest.param(Operators.ANY_OF, "false", ["not_blocked", "other_team_block"], id="any-of-not-blocked"),
        pytest.param(
            Operators.ANY_OF,
            "true~false",
            ["blocked", "blocked_again", "not_blocked", "other_team_block"],
            id="any-of-both",
        ),
        pytest.param(Operators.EXCLUDES, "true", ["not_blocked", "other_team_block"], id="excludes-blocked"),
        pytest.param(Operators.EXCLUDES, "false", ["blocked", "blocked_again"], id="excludes-not-blocked"),
        pytest.param(
            Operators.ALL_OF,
            "true",
            ["blocked", "blocked_again", "not_blocked", "other_team_block"],
            id="all-of-has-no-effect",
        ),
    ],
)
def test_participant_blocked_filter(
    sessions, filter_class, queryset_factory, query_param, row_id, operator, value, expected
):
    result = _apply(
        filter_class=filter_class,
        queryset=queryset_factory(sessions.team),
        query_param=query_param,
        value=value,
        operator=operator,
    )

    # blocked and blocked_again share a participant, so the participant table lists it once.
    expected_ids = sorted({row_id(getattr(sessions, name)) for name in expected})
    assert sorted(result.values_list("id", flat=True)) == expected_ids


@FILTER_TABLES
def test_blocked_filter_still_applies_when_the_flag_is_off(
    sessions, filter_class, queryset_factory, query_param, row_id
):
    """Saved filters keep filtering after the flag is turned off."""
    result = _apply(
        filter_class=filter_class,
        queryset=queryset_factory(sessions.team),
        query_param=query_param,
        value="true",
        operator=Operators.ANY_OF,
        flag_active=False,
    )

    expected_ids = sorted({row_id(sessions.blocked), row_id(sessions.blocked_again)})
    assert sorted(result.values_list("id", flat=True)) == expected_ids
