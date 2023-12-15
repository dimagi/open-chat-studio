from unittest import mock

import pytest

from apps.analysis.models import Analysis, AnalysisRun, RunGroup, RunStatus
from apps.analysis.tasks import PipelineSplitSignal, RunStatusContext, run_context
from apps.service_providers.models import LlmProvider


@pytest.fixture()
def llm_provider(team):
    return LlmProvider.objects.create(
        name="test",
        type="openai",
        team=team,
        config={
            "openai_api_key": "123123123",
        },
    )


@pytest.fixture()
def analysis(team, llm_provider):
    return Analysis.objects.create(
        team=team,
        name="test",
        source="test",
        llm_provider=llm_provider,
        llm_model="test",
    )


@pytest.fixture()
def mock_run_group():
    analysis = Analysis(llm_provider=LlmProvider(), llm_model="test")
    analysis.llm_provider.get_llm_service = mock.MagicMock()
    group = RunGroup(analysis=analysis, params={})
    group.save = mock.MagicMock()
    return group


@pytest.fixture()
def mock_analysis_run(mock_run_group):
    run = AnalysisRun(group=mock_run_group)
    run.save = mock.MagicMock()
    return run


@pytest.mark.parametrize(
    "params, expected",
    [
        ({}, {"llm_model": "test"}),
        ({"llm_model": "test2"}, {"llm_model": "test"}),
        ({"foo": "bar"}, {"llm_model": "test", "foo": "bar"}),
    ],
)
# @pytest.mark.django_db
def test_run_context_params(params, expected, mock_analysis_run):
    mock_analysis_run.group.params = params
    with run_context(mock_analysis_run) as pipeline_context:
        assert pipeline_context.params == expected


def test_run_context(mock_analysis_run):
    with run_context(mock_analysis_run) as pipeline_context:
        assert mock_analysis_run.start_time is not None
        assert mock_analysis_run.status == RunStatus.RUNNING
        pipeline_context.log.info("test log")
        with pipeline_context.log("magic"):
            pipeline_context.log.info("test log 2")

    assert mock_analysis_run.end_time is not None
    assert mock_analysis_run.status == RunStatus.SUCCESS
    logs = [(entry["logger"], entry["message"]) for entry in mock_analysis_run.log["entries"]]
    assert logs == [
        ("root", "test log"),
        ("magic", "test log 2"),
    ]


def test_run_context_error(mock_analysis_run):
    with pytest.raises(Exception):
        with run_context(mock_analysis_run):
            raise Exception("test exception")

    assert mock_analysis_run.end_time is not None
    assert mock_analysis_run.status == RunStatus.ERROR
    assert mock_analysis_run.error == """Exception('test exception')"""


def test_run_status_context(mock_run_group):
    with RunStatusContext(mock_run_group):
        assert mock_run_group.start_time is not None
        assert mock_run_group.status == RunStatus.RUNNING

    assert mock_run_group.end_time is not None
    assert mock_run_group.status == RunStatus.SUCCESS
    assert mock_run_group.error == ""


def test_run_status_context_error(mock_run_group):
    with RunStatusContext(mock_run_group, bubble_errors=False):
        raise Exception("test exception")

    assert mock_run_group.end_time is not None
    assert mock_run_group.status == RunStatus.ERROR
    assert mock_run_group.error == """Exception('test exception')"""


def test_run_status_context_split(mock_run_group):
    with RunStatusContext(mock_run_group):
        raise PipelineSplitSignal()

    assert mock_run_group.end_time is None
    assert mock_run_group.status == RunStatus.RUNNING
    assert mock_run_group.error == ""
