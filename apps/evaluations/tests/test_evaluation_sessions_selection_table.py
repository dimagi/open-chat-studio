import pytest

from apps.evaluations.tables import EvaluationSessionsSelectionTable
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_table_has_single_selection_column():
    """The Add Sessions table should expose only one checkbox column."""
    ExperimentSessionFactory.create()
    table = EvaluationSessionsSelectionTable(ExperimentSession.objects.all())
    column_names = list(table.columns.names())
    assert "selection" in column_names
    assert "clone_filtered_only" not in column_names


@pytest.mark.django_db()
def test_selection_column_header_has_no_label():
    """The remaining selection column header should be unlabeled (bare checkbox)."""
    ExperimentSessionFactory.create()
    table = EvaluationSessionsSelectionTable(ExperimentSession.objects.all())
    assert table.columns["selection"].column.verbose_name == ""
