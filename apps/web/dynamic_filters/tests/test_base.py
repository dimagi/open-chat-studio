import pytest

from apps.experiments.filters import ExperimentSessionFilter
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.web.dynamic_filters.datastructures import FilterParams


@pytest.mark.django_db()
def test_apply_does_not_emit_distinct_when_no_filters_applied():
    """The filter base class must not unconditionally add SELECT DISTINCT."""
    session = ExperimentSessionFactory.create()
    queryset = session.experiment.sessions.all()
    filtered = ExperimentSessionFilter().apply(queryset, FilterParams())
    sql = str(filtered.query).upper()
    assert "DISTINCT" not in sql, sql
