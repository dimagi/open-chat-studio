import pytest
from waffle.testutils import override_flag

from apps.experiments.filters import ExperimentSessionFilter
from apps.participants.filters import ParticipantFilter
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.web.dynamic_filters.base import ChoiceColumnFilter, Operators
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
