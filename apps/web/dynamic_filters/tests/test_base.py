from collections.abc import Sequence
from typing import ClassVar
from urllib.parse import urlencode

import pytest
from django.http import QueryDict
from waffle.testutils import override_flag

from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.participants.filters import ParticipantFilter
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.web.dynamic_filters.base import (
    ChoiceColumnFilter,
    ColumnFilter,
    MultiColumnFilter,
    Operators,
    StringColumnFilter,
    get_filter_schema,
)
from apps.web.dynamic_filters.datastructures import FilterParams


@pytest.mark.django_db()
def test_apply_does_not_emit_distinct_when_no_filters_applied():
    """The filter base class must not unconditionally add SELECT DISTINCT."""
    session = ExperimentSessionFactory.create()
    queryset = session.experiment.sessions.all()
    filtered = ExperimentSessionFilter().apply(queryset, FilterParams())
    sql = str(filtered.query).upper()
    assert "DISTINCT" not in sql, sql


def test_prepare_default_returns_self():
    """A filter with no prepare() override (most of them) must still satisfy the contract that
    .prepare() returns a filter, not None. See #4363."""
    f = ChoiceColumnFilter(query_param="x", label="X")

    assert f.prepare(team=None) is f


@pytest.mark.django_db()
def test_columns_handles_a_mix_of_overridden_and_default_prepare(team):
    """ExperimentSessionFilter.filters mixes filters that override prepare() (VersionsFilter,
    ChannelsFilter, ...) with ones that rely on the base no-op. columns() must not crash on
    either kind. See #4363."""
    with override_flag("flag_human_annotations", active=True):
        columns = ExperimentSessionFilter.columns(team)

    assert set(columns) == {f.query_param for f in ExperimentSessionFilter.filters}


@pytest.mark.parametrize(
    ("filter_class", "query_param", "has_any_of"),
    [
        pytest.param(ExperimentSessionFilter, "participant", True, id="session-participant"),
        pytest.param(ExperimentSessionFilter, "session_id", False, id="session-id"),
        pytest.param(ParticipantFilter, "participant", True, id="participants-page-participant"),
        pytest.param(ParticipantFilter, "remote_id", True, id="participants-page-remote-id"),
    ],
)
def test_string_columns_offer_any_of_only_when_allowed(filter_class, query_param, has_any_of):
    column = next(f for f in filter_class.filters if f.query_param == query_param)

    assert (Operators.ANY_OF in column.model_dump()["operators"]) is has_any_of


@pytest.mark.parametrize(
    ("query_param", "has_any_of"),
    [
        pytest.param("participant", True, id="participant"),
        pytest.param("session_id", False, id="session-id"),
    ],
)
def test_filter_schema_offers_any_of_only_when_allowed(query_param, has_any_of):
    schema = get_filter_schema(ExperimentSessionFilter)

    assert (Operators.ANY_OF.value in schema[query_param]["operators"]) is has_any_of


class _FlagGatedFilter(MultiColumnFilter):
    filters: ClassVar[Sequence[ColumnFilter]] = [
        StringColumnFilter(query_param="open", label="Open", columns=["external_id"]),
        StringColumnFilter(
            query_param="gated", label="Gated", columns=["external_id"], required_flag=Flags.HUMAN_ANNOTATIONS.slug
        ),
    ]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("flag_active", "expected"),
    [pytest.param(True, {"open", "gated"}, id="flag-on"), pytest.param(False, {"open"}, id="flag-off")],
)
def test_columns_offer_a_flag_gated_filter_only_when_the_flag_is_on(team, flag_active, expected):
    with override_flag(Flags.HUMAN_ANNOTATIONS.slug, active=flag_active):
        columns = _FlagGatedFilter.columns(team)

    assert set(columns) == expected
    assert all("required_flag" not in column for column in columns.values())


@pytest.mark.django_db()
@pytest.mark.parametrize("flag_active", [pytest.param(True, id="flag-on"), pytest.param(False, id="flag-off")])
def test_apply_applies_a_flag_gated_filter_whether_or_not_the_flag_is_on(team, flag_active):
    """Saved filters that use a hidden filter keep working after its flag is turned off."""
    matching = ExperimentSessionFactory.create(team=team, external_id="match")
    ExperimentSessionFactory.create(team=team, external_id="other")
    params = FilterParams(QueryDict(urlencode({"f_gated": "match", "op_gated": Operators.EQUALS})))

    with override_flag(Flags.HUMAN_ANNOTATIONS.slug, active=flag_active):
        result = _FlagGatedFilter().apply(ExperimentSession.objects.filter(team=team), filter_params=params)

    assert set(result.values_list("id", flat=True)) == {matching.id}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("flag_active", "expected"),
    [pytest.param(True, {"open", "gated"}, id="flag-on"), pytest.param(False, {"open"}, id="flag-off")],
)
def test_filter_schema_for_a_team_leaves_out_flag_gated_filters_when_the_flag_is_off(team, flag_active, expected):
    with override_flag(Flags.HUMAN_ANNOTATIONS.slug, active=flag_active):
        schema = get_filter_schema(_FlagGatedFilter, team=team)

    assert set(schema) == expected


def test_filter_schema_without_a_team_lists_every_filter():
    assert set(get_filter_schema(_FlagGatedFilter)) == {"open", "gated"}
