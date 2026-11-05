"""Tests for the Annotation Queue session filter, including the 'Not in any queue' option."""

from types import SimpleNamespace

import pytest
from django.http import QueryDict
from waffle.testutils import override_flag

from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.column_filters import NOT_IN_ANY_QUEUE, AnnotationQueueFilter
from apps.web.dynamic_filters.datastructures import FilterParams


def _filter_sessions(team, value, operator):
    """Apply one Annotation Queue filter row to the team's sessions."""
    query_dict = QueryDict("", mutable=True)
    query_dict.update({"f_annotation_queue": value, "op_annotation_queue": operator})
    queryset = ExperimentSession.objects.filter(team=team)
    return ExperimentSessionFilter().apply(queryset, FilterParams(query_dict))


def _session(team):
    """A session whose chat and experiment belong to the given team."""
    return ExperimentSessionFactory.create(team=team, chat__team=team, experiment__team=team)


@pytest.fixture()
def queued(db):
    """A team with two queues and one session in each, plus one session in no queue."""
    team = TeamFactory.create()
    safety = AnnotationQueueFactory.create(team=team, name="Safety QA")
    tone = AnnotationQueueFactory.create(team=team, name="Tone")
    in_safety, in_tone, in_none = _session(team), _session(team), _session(team)
    AnnotationItemFactory.create(queue=safety, team=team, session=in_safety)
    AnnotationItemFactory.create(queue=tone, team=team, session=in_tone)
    return SimpleNamespace(team=team, safety=safety, tone=tone, in_safety=in_safety, in_tone=in_tone, in_none=in_none)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param(Operators.ANY_OF, "none", {"in_none"}, id="any-of-none"),
        pytest.param(Operators.EXCLUDES, "none", {"in_safety", "in_tone"}, id="excludes-none"),
        pytest.param(Operators.ANY_OF, "none~SAFETY", {"in_none", "in_safety"}, id="any-of-none-and-queue"),
        pytest.param(Operators.EXCLUDES, "none~SAFETY", {"in_tone"}, id="excludes-none-and-queue"),
        pytest.param(Operators.ANY_OF, "SAFETY", {"in_safety"}, id="any-of-queue"),
        pytest.param(Operators.EXCLUDES, "SAFETY", {"in_tone", "in_none"}, id="excludes-queue"),
        pytest.param(Operators.ANY_OF, "SAFETY~TONE", {"in_safety", "in_tone"}, id="any-of-two-queues"),
        pytest.param(Operators.ALL_OF, "SAFETY~TONE", set(), id="all-of-two-queues-no-session-in-both"),
    ],
)
def test_annotation_queue_filter_semantics(queued, operator, value, expected):
    """Ticked options combine as a plain OR, and excludes is the complement of any-of."""
    value = value.replace("SAFETY", str(queued.safety.id)).replace("TONE", str(queued.tone.id))

    result = _filter_sessions(queued.team, value, operator)

    expected_ids = {getattr(queued, name).id for name in expected}
    assert set(result.values_list("id", flat=True)) == expected_ids


@pytest.mark.django_db()
def test_annotation_queue_filter_returns_a_multi_queue_session_once(queued):
    """A session in two queues matches once, not once per queue."""
    AnnotationItemFactory.create(queue=queued.tone, team=queued.team, session=queued.in_safety)

    result = _filter_sessions(queued.team, f"{queued.safety.id}~{queued.tone.id}", Operators.ANY_OF)

    assert list(result.values_list("id", flat=True)).count(queued.in_safety.id) == 1
    assert result.count() == 2


@pytest.mark.django_db()
def test_annotation_queue_filter_ignores_values_that_are_not_ids_or_none(queued):
    """Junk or a deleted queue id contributes nothing; 'none' alongside it still applies."""
    assert _filter_sessions(queued.team, "abc", Operators.ANY_OF).count() == 3
    assert _filter_sessions(queued.team, "999999", Operators.ANY_OF).count() == 0
    assert set(_filter_sessions(queued.team, "abc~none", Operators.ANY_OF).values_list("id", flat=True)) == {
        queued.in_none.id
    }


@pytest.mark.django_db()
def test_annotation_queue_filter_does_not_match_another_teams_queue(queued):
    """A queue id from another team matches none of this team's sessions."""
    other_team = TeamFactory.create()
    other_queue = AnnotationQueueFactory.create(team=other_team, name="Elsewhere")
    AnnotationItemFactory.create(queue=other_queue, team=other_team, session=_session(other_team))

    result = _filter_sessions(queued.team, str(other_queue.id), Operators.ANY_OF)

    assert result.count() == 0


@pytest.mark.django_db()
def test_annotation_queue_filter_options_start_with_none_then_queues_by_name(queued):
    """The picker shows 'Not in any queue' first, then this team's queues alphabetically."""
    AnnotationQueueFactory.create(team=TeamFactory.create(), name="Elsewhere")
    options = AnnotationQueueFilter().prepare(queued.team).options

    assert options == [
        {"id": NOT_IN_ANY_QUEUE, "label": "Not in any queue"},
        {"id": queued.safety.id, "label": "Safety QA"},
        {"id": queued.tone.id, "label": "Tone"},
    ]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("flag_active", "expected"),
    [pytest.param(True, True, id="flag-on"), pytest.param(False, False, id="flag-off")],
)
def test_annotation_queue_filter_is_offered_only_when_the_flag_is_on(queued, flag_active, expected):
    """The column list the picker renders includes the filter only for teams with human annotations."""
    with override_flag("flag_human_annotations", active=flag_active):
        columns = ExperimentSessionFilter.columns(queued.team)

    assert ("annotation_queue" in columns) is expected


@pytest.mark.django_db()
def test_annotation_queue_filter_still_applies_when_flag_is_off(queued):
    """A stored filter keeps working after the flag is turned off, so saved filters never error."""
    with override_flag("flag_human_annotations", active=False):
        result = _filter_sessions(queued.team, "none", Operators.ANY_OF)

    assert set(result.values_list("id", flat=True)) == {queued.in_none.id}


@pytest.mark.django_db()
def test_annotation_queue_filter_excludes_a_session_that_is_also_in_another_queue(queued):
    """Excluding one queue drops a session in that queue even when it is in others too."""
    AnnotationItemFactory.create(queue=queued.tone, team=queued.team, session=queued.in_safety)

    result = _filter_sessions(queued.team, str(queued.safety.id), Operators.EXCLUDES)

    assert set(result.values_list("id", flat=True)) == {queued.in_tone.id, queued.in_none.id}
