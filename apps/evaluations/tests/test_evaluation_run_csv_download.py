import csv
import io
from unittest.mock import Mock, patch

import pytest
from django.urls import reverse

from apps.evaluations.evaluators import EvaluatorResult
from apps.evaluations.models import EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tasks import export_evaluation_run_results_task
from apps.files.models import File, FilePurpose
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)


def _evaluator_output(result: dict, generated_response: str, context: dict | None = None) -> dict:
    return EvaluatorResult(
        message={
            "input": {"content": "What is AI?", "role": "human"},
            "output": {"content": "Artificial Intelligence", "role": "ai"},
            "context": context or {},
            "history": [],
            "metadata": {},
        },
        result=result,
        generated_response=generated_response,
    ).model_dump()


def _read_csv_rows(file_id: int) -> tuple[list[str], list[dict]]:
    content = File.objects.get(id=file_id).file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    return list(reader.fieldnames or []), rows


@pytest.fixture(autouse=True)
def _stub_progress_recorder():
    """ProgressRecorder needs a live Celery request; these tests call the task directly."""
    with patch("apps.evaluations.tasks.ProgressRecorder"):
        yield


def _completed_run(team, config=None):
    return EvaluationRunFactory.create(
        team=team,
        config=config or EvaluationConfigFactory.create(team=team),
        status=EvaluationRunStatus.COMPLETED,
        type=EvaluationRunType.FULL,
    )


@pytest.mark.django_db()
def test_export_evaluation_run_results_task_creates_csv_file():
    config = EvaluationConfigFactory.create()
    team = config.team
    evaluator = EvaluatorFactory.create(team=team)
    run = _completed_run(team, config)
    EvaluationResultFactory.create(
        team=team, run=run, evaluator=evaluator, output=_evaluator_output({"score": 8.5}, "Generated response")
    )

    result = export_evaluation_run_results_task(run.id, team.id)

    file = File.objects.get(id=result["file_id"])
    assert file.purpose == FilePurpose.DATA_EXPORT
    assert file.expiry_date is not None

    _, rows = _read_csv_rows(result["file_id"])
    assert len(rows) == 1
    assert rows[0][f"score ({evaluator.name})"] == "8.5"
    assert rows[0]["Generated Response"] == "Generated response"


@pytest.mark.django_db()
def test_export_evaluation_run_results_task_excludes_other_runs_of_the_same_config():
    config = EvaluationConfigFactory.create()
    team = config.team
    evaluator = EvaluatorFactory.create(team=team)
    run = _completed_run(team, config)
    other_run = _completed_run(team, config)
    EvaluationResultFactory.create(
        team=team, run=run, evaluator=evaluator, output=_evaluator_output({"score": 1.0}, "this run")
    )
    EvaluationResultFactory.create(
        team=team, run=other_run, evaluator=evaluator, output=_evaluator_output({"score": 2.0}, "other run")
    )

    result = export_evaluation_run_results_task(run.id, team.id)

    _, rows = _read_csv_rows(result["file_id"])
    assert len(rows) == 1
    assert rows[0]["Generated Response"] == "this run"


@pytest.mark.django_db()
def test_export_evaluation_run_results_task_includes_all_context_columns():
    """Every message's context keys become columns, blank where a message lacks the key."""
    config = EvaluationConfigFactory.create()
    team = config.team
    evaluator = EvaluatorFactory.create(team=team)
    run = _completed_run(team, config)
    EvaluationResultFactory.create(
        team=team,
        run=run,
        evaluator=evaluator,
        output=_evaluator_output(
            {"score": 8.5, "accuracy": 0.9}, "Generated AI response", {"user_location": "USA", "user_age": "25"}
        ),
    )
    EvaluationResultFactory.create(
        team=team,
        run=run,
        evaluator=evaluator,
        output=_evaluator_output(
            {"score": 7.0, "helpfulness": 0.8}, "Generated Python response", {"topic": "programming"}
        ),
    )

    result = export_evaluation_run_results_task(run.id, team.id)

    headers, rows = _read_csv_rows(result["file_id"])
    for column in ["topic", "user_age", "user_location"]:
        assert column in headers, f"Missing context column: {column}"
    for column in [f"accuracy ({evaluator.name})", f"helpfulness ({evaluator.name})", f"score ({evaluator.name})"]:
        assert column in headers, f"Missing evaluator column: {column}"

    assert len(rows) == 2
    assert rows[0]["user_location"] == "USA"
    assert rows[0]["topic"] == ""
    assert rows[0][f"helpfulness ({evaluator.name})"] == ""
    assert rows[1]["topic"] == "programming"
    assert rows[1]["user_location"] == ""
    assert rows[1][f"accuracy ({evaluator.name})"] == ""


@pytest.mark.django_db()
def test_export_evaluation_run_results_task_drives_progress_to_completion():
    """The UI bar is fed by the recorder, so the task must reach total/total."""
    config = EvaluationConfigFactory.create()
    team = config.team
    evaluator = EvaluatorFactory.create(team=team)
    run = _completed_run(team, config)
    for index in range(3):
        EvaluationResultFactory.create(
            team=team,
            run=run,
            evaluator=evaluator,
            message=EvaluationMessageFactory.create(),
            output=_evaluator_output({"score": index}, f"response {index}"),
        )

    with patch("apps.evaluations.tasks.ProgressRecorder") as recorder_class:
        export_evaluation_run_results_task(run.id, team.id)

    assert recorder_class.return_value.set_progress.call_args.args == (3, 3)


@pytest.mark.django_db()
def test_export_evaluation_run_results_task_returns_error_for_unknown_run():
    config = EvaluationConfigFactory.create()

    result = export_evaluation_run_results_task(0, config.team_id)

    assert "file_id" not in result
    assert result["error"]


@pytest.fixture()
def logged_in_client(client, team_with_users):
    client.force_login(team_with_users.members.first())
    return client


def _start_url(run):
    return reverse("evaluations:evaluation_run_download_start", args=[run.team.slug, run.config_id, run.id])


@pytest.mark.django_db()
def test_start_run_download_dispatches_the_export_task(logged_in_client, team_with_users):
    run = _completed_run(team_with_users)

    with patch("apps.evaluations.views.evaluation_config_views.export_evaluation_run_results_task.delay") as mock_delay:
        mock_delay.return_value = Mock(id="task-123")
        response = logged_in_client.post(_start_url(run))

    assert response.status_code == 200
    mock_delay.assert_called_once_with(run.id, team_with_users.id)
    assert reverse("celery_progress:task_status", args=["task-123"]) in response.content.decode()


@pytest.mark.django_db()
def test_start_run_download_404s_for_another_teams_run(logged_in_client, team_with_users):
    other_run = _completed_run(EvaluationConfigFactory.create().team)
    url = reverse(
        "evaluations:evaluation_run_download_start",
        args=[team_with_users.slug, other_run.config_id, other_run.id],
    )

    with patch("apps.evaluations.views.evaluation_config_views.export_evaluation_run_results_task.delay") as mock_delay:
        response = logged_in_client.post(url)

    assert response.status_code == 404
    mock_delay.assert_not_called()


@pytest.mark.django_db()
def test_evaluation_runs_table_renders_a_download_button_per_run(logged_in_client, team_with_users):
    run = _completed_run(team_with_users)
    url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, run.config_id])

    response = logged_in_client.get(url)

    content = response.content.decode()
    assert f'id="export-status-{run.id}"' in content
    assert _start_url(run) in content


@pytest.mark.django_db()
def test_evaluation_runs_table_disables_the_button_for_a_running_run(logged_in_client, team_with_users):
    run = EvaluationRunFactory.create(
        team=team_with_users,
        config=EvaluationConfigFactory.create(team=team_with_users),
        status=EvaluationRunStatus.PROCESSING,
    )
    url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, run.config_id])

    content = logged_in_client.get(url).content.decode()

    button = content[content.index("data-export-start") : content.index(f'id="export-status-{run.id}"')]
    assert "disabled" in button


@pytest.mark.django_db()
def test_results_page_renders_the_download_button_and_progress_library(logged_in_client, team_with_users):
    run = _completed_run(team_with_users)
    url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, run.config_id, run.id])

    content = logged_in_client.get(url).content.decode()

    assert _start_url(run) in content
    # The swapped-in progress fragment calls CeleryProgressBar, so the page has to load it.
    assert "celery_progress/celery_progress.js" in content


@pytest.mark.django_db()
def test_upload_results_page_renders_the_template_download_button(logged_in_client, team_with_users):
    run = _completed_run(team_with_users)
    url = reverse("evaluations:evaluation_run_update", args=[team_with_users.slug, run.config_id, run.id])

    content = logged_in_client.get(url).content.decode()

    assert _start_url(run) in content
    assert "Download Template" in content
