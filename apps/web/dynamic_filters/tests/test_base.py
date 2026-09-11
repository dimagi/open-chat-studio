import pytest

from apps.experiments.filters import ExperimentSessionFilter
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.web.dynamic_filters.base import ChoiceColumnFilter
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
    columns = ExperimentSessionFilter.columns(team)

    assert set(columns) == {f.query_param for f in ExperimentSessionFilter.filters}
