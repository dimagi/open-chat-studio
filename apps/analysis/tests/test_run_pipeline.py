from unittest import mock

import pytest
from django.core.files.base import ContentFile

from apps.analysis.core import Pipeline
from apps.analysis.models import Resource, ResourceType, RunStatus
from apps.analysis.steps.loaders import ResourceTextLoader
from apps.analysis.tasks import run_analysis, run_pipline_split
from apps.analysis.tests.demo_steps import StrReverse, TokenizeStr
from apps.analysis.tests.factories import RunGroupFactory

INPUT_DATA = "here is some text to play with"


@pytest.fixture()
def resource(team_with_users):
    resource = Resource.objects.create(
        team=team_with_users,
        name="test text",
        type=ResourceType.TEXT,
    )
    resource.file.save(f"{resource.name}.txt", ContentFile(INPUT_DATA))
    return resource


@pytest.fixture()
def run_group(resource):
    run_group = RunGroupFactory(team=resource.team, params={"resource_id": resource.id, "prompt": "test"})
    run_group.analysis.llm_provider.get_llm_service = mock.Mock()
    return run_group


@mock.patch("apps.analysis.tasks.get_data_pipeline")
def test_run_analysis_with_valid_run_group(mock_get_data_pipeline, run_group):
    mock_get_data_pipeline.return_value = Pipeline([StrReverse()])

    run_analysis(run_group.id)
    run_group.refresh_from_db()
    assert run_group.error == ""
    assert run_group.status == RunStatus.SUCCESS
    assert run_group.start_time is not None
    assert run_group.end_time is not None

    runs = list(run_group.analysisrun_set.all())
    assert len(runs) == 2
    check_run(runs[0], RunStatus.SUCCESS)
    check_run(runs[1], RunStatus.SUCCESS)
    assert runs[0].output_summary == INPUT_DATA
    assert runs[1].output_summary == INPUT_DATA[::-1]  # reverse the input


@mock.patch("apps.analysis.tasks.get_data_pipeline")
@mock.patch("apps.analysis.tasks.get_source_pipeline")
def test_run_analysis_with_split_pipeline(mock_get_source_pipeline, mock_get_data_pipeline, run_group):
    mock_get_source_pipeline.return_value = Pipeline([ResourceTextLoader(), TokenizeStr()])
    mock_get_data_pipeline.return_value = Pipeline([StrReverse()])

    run_analysis(run_group.id)
    run_group.refresh_from_db()
    assert run_group.error == ""
    assert run_group.status == RunStatus.RUNNING
    assert run_group.start_time is not None
    assert run_group.end_time is None

    runs = list(run_group.analysisrun_set.all())
    assert len(runs) == 8
    check_run(runs[0], RunStatus.SUCCESS)
    assert f"{len(INPUT_DATA.split())} groups created" in runs[0].output_summary

    # run each step manually since celery isn't running
    tokens = INPUT_DATA.split()
    for index, run in enumerate(runs[1:]):
        run_pipline_split(run.id)
        run.refresh_from_db()
        assert run.error == ""
        check_run(run, RunStatus.SUCCESS)
        assert run.output_summary == tokens[index][::-1]


def check_run(run, status):
    assert run.status == status
    assert run.start_time is not None
    assert run.end_time is not None
