import pytest

from apps.analysis.models import Analysis, AnalysisRun, RunStatus
from apps.analysis.tasks import run_context
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


@pytest.mark.parametrize(
    "params, expected",
    [
        ({}, {"llm_model": "test"}),
        ({"llm_model": "test2"}, {"llm_model": "test"}),
        ({"foo": "bar"}, {"llm_model": "test", "foo": "bar"}),
    ],
)
@pytest.mark.django_db
def test_run_context_params(params, expected, team, analysis):
    run = AnalysisRun.objects.create(
        team=team,
        analysis=analysis,
        params=params,
    )
    with run_context(run) as pipeline_context:
        assert pipeline_context.params == expected


def test_run_context(team, analysis):
    run = AnalysisRun.objects.create(
        team=team,
        analysis=analysis,
        params={},
    )
    with run_context(run) as pipeline_context:
        run.refresh_from_db()
        assert run.start_time is not None
        assert run.status == RunStatus.RUNNING
        pipeline_context.log.info("test log")
        with pipeline_context.log("magic"):
            pipeline_context.log.info("test log 2")

    run.refresh_from_db()
    assert run.end_time is not None
    assert run.status == RunStatus.SUCCESS
    logs = [(entry["logger"], entry["message"]) for entry in run.log["entries"]]
    assert logs == [
        ("root", "test log"),
        ("magic", "test log 2"),
    ]


def test_run_context_error(team, analysis):
    run = AnalysisRun.objects.create(
        team=team,
        analysis=analysis,
        params={},
    )
    with run_context(run):
        raise Exception("test exception")

    run.refresh_from_db()
    assert run.end_time is not None
    assert run.status == RunStatus.ERROR
    assert run.error == """Exception('test exception')"""
