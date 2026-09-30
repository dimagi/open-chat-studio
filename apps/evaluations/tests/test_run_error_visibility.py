"""Failed messages in a run are counted, flagged and filterable, and a run with many of them warns the team."""

import pytest
from django.urls import reverse

from apps.evaluations.const import FAILED_FILTER_PARAM
from apps.evaluations.models import EvaluationRunStatus, EvaluationRunType
from apps.evaluations.notifications import evaluation_run_outcome_notification
from apps.evaluations.tasks import finalize_evaluation_run
from apps.ocs_notifications.models import NotificationEvent
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)

QUOTA_ERROR = (
    "The LLM provider account has no credit or quota remaining: You have reached your specified API usage limits."
)
GENERATION_FAILED = {
    "result": {"score": 0},
    "generated_response": "",
    "generation_error": QUOTA_ERROR,
    "generation_error_category": "quota_exhausted",
}
SUCCEEDED = {"result": {"score": 1}, "generated_response": "hi"}
FAILED_FILTER = {FAILED_FILTER_PARAM: "1"}


@pytest.fixture()
def evaluator(team_with_users):
    return EvaluatorFactory.create(team=team_with_users, name="Judge")


@pytest.fixture()
def run(team_with_users, evaluator):
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    return EvaluationRunFactory.create(
        team=team_with_users, config=config, evaluator_ids=[evaluator.id], status=EvaluationRunStatus.COMPLETED
    )


@pytest.fixture()
def logged_in_client(client, team_with_users):
    client.force_login(team_with_users.members.first())
    return client


def _add_results(run, evaluator, *, failed: int, succeeded: int):
    outputs = [GENERATION_FAILED] * failed + [SUCCEEDED] * succeeded
    return [
        EvaluationResultFactory.create(
            run=run, team=run.team, evaluator=evaluator, message=EvaluationMessageFactory.create(), output=output
        )
        for output in outputs
    ]


def _home_url(run):
    return reverse("evaluations:evaluation_results_home", args=[run.team.slug, run.config_id, run.id])


def _table_url(run):
    return reverse("evaluations:evaluation_results_table", args=[run.team.slug, run.config_id, run.id])


@pytest.mark.django_db()
class TestRunSummary:
    def test_header_counts_failed_results_and_names_the_category(self, logged_in_client, run, evaluator):
        _add_results(run, evaluator, failed=3, succeeded=7)

        response = logged_in_client.get(_home_url(run))

        content = response.content.decode()
        assert response.context["error_summary"].failed_count == 3
        assert 'data-testid="run-error-summary"' in content
        assert "3 of 10 messages failed" in content
        assert "Generation failed: Provider quota exhausted" in content
        assert 'data-testid="run-error-warning"' in content

    def test_errors_below_the_threshold_are_counted_without_a_warning(self, logged_in_client, run, evaluator):
        _add_results(run, evaluator, failed=1, succeeded=39)

        content = logged_in_client.get(_home_url(run)).content.decode()

        assert 'data-testid="run-error-summary"' in content
        assert 'data-testid="run-error-warning"' not in content

    def test_page_link_with_the_errors_filter_loads_the_table_filtered(self, logged_in_client, run, evaluator):
        """The notification links here, so the filter has to reach the table the page loads."""
        _add_results(run, evaluator, failed=3, succeeded=7)

        response = logged_in_client.get(_home_url(run), FAILED_FILTER)

        assert response.context["table_url"] == f"{_table_url(run)}?failed=1"

    def test_clean_run_shows_nothing_about_errors(self, logged_in_client, run, evaluator):
        _add_results(run, evaluator, failed=0, succeeded=5)

        content = logged_in_client.get(_home_url(run)).content.decode()

        assert 'data-testid="run-error-summary"' not in content
        assert 'data-testid="run-error-warning"' not in content


@pytest.mark.django_db()
class TestResultsTable:
    @pytest.mark.parametrize(
        ("output", "failed"),
        [
            pytest.param(GENERATION_FAILED, True, id="generation-failed"),
            pytest.param({"error": "boom", "error_category": "other"}, True, id="evaluator-error"),
            pytest.param(SUCCEEDED, False, id="succeeded"),
            pytest.param(
                {**SUCCEEDED, "message": {"input": {}, "output": {}, "context": {"error": "a field in the dataset"}}},
                False,
                id="dataset-context-field-named-error",
            ),
        ],
    )
    def test_failed_rows_carry_a_badge_and_an_errors_pill(self, logged_in_client, run, evaluator, output, failed):
        message = EvaluationMessageFactory.create(context={"error": "a field in the dataset"})
        EvaluationResultFactory.create(run=run, team=run.team, evaluator=evaluator, message=message, output=output)
        _add_results(run, evaluator, failed=0, succeeded=1)

        response = logged_in_client.get(_table_url(run))

        assert response.content.decode().count('data-testid="result-error-badge"') == int(failed)
        assert any(pill.failed for pill in response.context["filter_pills"]) is failed

    def test_errors_pill_narrows_the_table_to_failed_rows(self, logged_in_client, run, evaluator):
        _add_results(run, evaluator, failed=2, succeeded=3)

        response = logged_in_client.get(_table_url(run), FAILED_FILTER)

        errors_pill = next(pill for pill in response.context["filter_pills"] if pill.failed)
        assert errors_pill.active is True
        assert errors_pill.label == "Errors (2)"
        assert len(response.context["table"].rows) == 2

    def test_each_pill_clears_the_other_filter(self, logged_in_client, team_with_users):
        """Moving from the Errors pill to a value pill, or back, must not combine the two filters."""
        judge = EvaluatorFactory.create(
            team=team_with_users,
            name="Judge",
            params={
                "llm_prompt": "x",
                "output_schema": {"verdict": {"type": "choice", "description": "x", "choices": ["Good", "Bad"]}},
            },
        )
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[judge])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[judge.id])
        EvaluationResultFactory.create(run=run, team=run.team, evaluator=judge, output={"result": {"verdict": "Good"}})
        EvaluationResultFactory.create(run=run, team=run.team, evaluator=judge, output=GENERATION_FAILED)

        errors_view = logged_in_client.get(_table_url(run), FAILED_FILTER).content.decode()
        value_view = logged_in_client.get(
            _table_url(run), {"filter_field": "verdict (Judge)", "filter_value": "Good"}
        ).content.decode()

        assert f'hx-get="{_table_url(run)}?filter_field=verdict+%28Judge%29&amp;filter_value=Good"' in errors_view
        assert f'hx-get="{_table_url(run)}?failed=1"' in value_view


@pytest.mark.django_db()
class TestCountsAgree:
    """The stat, the warning and the Errors pill all count failed messages, whatever the number of evaluators."""

    def test_two_evaluators_and_a_failed_generation(self, logged_in_client, team_with_users):
        judges = [EvaluatorFactory.create(team=team_with_users, name=name) for name in ("Judge A", "Judge B")]
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=judges)
        run = EvaluationRunFactory.create(
            team=team_with_users,
            config=config,
            evaluator_ids=[judge.id for judge in judges],
            status=EvaluationRunStatus.COMPLETED,
        )
        messages = [EvaluationMessageFactory.create() for _ in range(4)]
        for index, message in enumerate(messages):
            for judge in judges:
                EvaluationResultFactory.create(
                    run=run,
                    team=run.team,
                    evaluator=judge,
                    message=message,
                    output=GENERATION_FAILED if index == 0 else SUCCEEDED,
                )

        home = logged_in_client.get(_home_url(run))
        table = logged_in_client.get(_table_url(run))

        assert home.context["error_summary"].failed_count == 1
        assert "1 of 4 messages failed" in home.content.decode()
        errors_pill = next(pill for pill in table.context["filter_pills"] if pill.failed)
        assert errors_pill.label == "Errors (1)"


@pytest.mark.django_db()
class TestResultDetail:
    def _detail_url(self, run, message_id):
        return reverse("evaluations:evaluation_result_detail", args=[run.team.slug, run.config_id, run.id, message_id])

    def test_panel_shows_why_the_result_failed(self, logged_in_client, run, evaluator):
        (result,) = _add_results(run, evaluator, failed=1, succeeded=0)

        content = logged_in_client.get(self._detail_url(run, result.message_id)).content.decode()

        assert "Generation failed" in content
        assert "Provider quota exhausted" in content
        assert "reached your specified API usage limits" in content

    def test_panel_names_the_evaluator_that_failed(self, logged_in_client, run, evaluator):
        output = {"error": "invalid x-api-key", "error_category": "authentication"}
        result = EvaluationResultFactory.create(run=run, team=run.team, evaluator=evaluator, output=output)

        content = logged_in_client.get(self._detail_url(run, result.message_id)).content.decode()

        assert "Evaluator failed (Judge): Provider credentials rejected" in content

    def test_prev_next_stay_within_the_errors_filter(self, logged_in_client, run, evaluator):
        first, _ok, second = _add_results(run, evaluator, failed=1, succeeded=1) + _add_results(
            run, evaluator, failed=1, succeeded=0
        )

        response = logged_in_client.get(self._detail_url(run, first.message_id), FAILED_FILTER)

        assert f"/{second.message_id}/" in response.context["next_url"]


@pytest.mark.django_db()
class TestCompletionNotification:
    def test_notifies_the_team_when_the_run_has_many_failures(self, run, evaluator):
        _add_results(run, evaluator, failed=3, succeeded=7)

        finalize_evaluation_run(run.id)

        event = NotificationEvent.objects.get(team=run.team)
        assert "3 of 10" in event.message
        assert "Provider quota exhausted" in event.message
        assert next(iter(event.links.values())).endswith("?failed=1")

    def test_no_notification_below_the_threshold(self, run, evaluator):
        _add_results(run, evaluator, failed=1, succeeded=39)

        finalize_evaluation_run(run.id)

        assert not NotificationEvent.objects.filter(team=run.team).exists()

    @pytest.mark.parametrize("status", [EvaluationRunStatus.PENDING, EvaluationRunStatus.PROCESSING])
    def test_no_notification_before_the_run_ends(self, run, evaluator, status):
        _add_results(run, evaluator, failed=3, succeeded=7)
        run.status = status

        evaluation_run_outcome_notification(run)

        assert not NotificationEvent.objects.filter(team=run.team).exists()

    def test_no_notification_for_a_preview(self, run, evaluator):
        run.type = EvaluationRunType.PREVIEW
        run.save(update_fields=["type"])
        _add_results(run, evaluator, failed=3, succeeded=7)

        finalize_evaluation_run(run.id)

        assert not NotificationEvent.objects.filter(team=run.team).exists()

    def test_finalizing_twice_notifies_once(self, run, evaluator):
        _add_results(run, evaluator, failed=3, succeeded=7)

        finalize_evaluation_run(run.id)
        finalize_evaluation_run(run.id)

        assert NotificationEvent.objects.filter(team=run.team).count() == 1

    def test_a_failing_notification_does_not_block_finalization(self, run, evaluator, monkeypatch):
        _add_results(run, evaluator, failed=3, succeeded=7)

        def _boom(**_kwargs):
            raise RuntimeError("notifications down")

        monkeypatch.setattr("apps.evaluations.notifications.create_notification", _boom)

        finalize_evaluation_run(run.id)

        run.refresh_from_db()
        assert run.finalized_at is not None
