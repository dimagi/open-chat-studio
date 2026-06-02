"""View tests for the undo_evaluation_run_tags endpoint."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.evaluations.models import (
    ConditionType,
    EvaluationMode,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
)
from apps.evaluations.tagging import apply_rules_to_result, archive_superseded_runs, reverse_stale_tags
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)

NEG = {"result": {"sentiment": "negative"}}
POS = {"result": {"sentiment": "positive"}}


def _flash_messages(response):
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def _setup_tagging_config(team):
    """Config with two opposite-sentiment rules ('bad'/'good') over a MESSAGE-mode evaluator."""
    evaluator = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
    EvaluatorTagRuleFactory.create(
        team=team,
        evaluator=evaluator,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "negative"},
        tag__name="bad",
    )
    EvaluatorTagRuleFactory.create(
        team=team,
        evaluator=evaluator,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "positive"},
        tag__name="good",
    )
    dataset = EvaluationDatasetFactory.create(team=team, messages=[])
    config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
    return config, evaluator


def _complete_run(config, evaluator, results, run_type, finished_at):
    """Create + finish a run that evaluated `results` (list of (message, output)).

    Mirrors mark_evaluation_complete: apply tag rules per result, reverse stale tags, then
    archive superseded runs. `finished_at` is stamped so runs order deterministically.
    """
    run = EvaluationRunFactory.create(
        team=config.team, config=config, status=EvaluationRunStatus.COMPLETED, type=run_type
    )
    EvaluationRun.objects.filter(pk=run.pk).update(created_at=finished_at, finished_at=finished_at)
    run.refresh_from_db()

    for message, output in results:
        result = EvaluationResultFactory.create(
            team=config.team, evaluator=evaluator, message=message, run=run, output=output
        )
        apply_rules_to_result(result, evaluator, message)

    reverse_stale_tags(run)
    archive_superseded_runs(run)
    return run


def _tags(message):
    return set(message.expected_output_chat_message.tags.values_list("name", flat=True))


@pytest.mark.django_db()
class TestUndoEvaluationRunTagsView:
    def _url(self, team, config, run):
        return reverse(
            "evaluations:evaluation_run_undo_tags",
            args=[team.slug, config.pk, run.pk],
        )

    def test_undo_on_processing_run_is_rejected(self, client, team_with_users):
        """A POST to undo a PROCESSING run is rejected: undo is not called and an error is flashed."""
        user = team_with_users.members.filter(membership__groups__name="Super Admin").first()
        config = EvaluationConfigFactory.create(team=team_with_users)
        run = EvaluationRunFactory.create(
            team=team_with_users,
            config=config,
            status=EvaluationRunStatus.PROCESSING,
            type=EvaluationRunType.FULL,
        )

        client.force_login(user)
        url = self._url(team_with_users, config, run)

        with patch("apps.evaluations.views.evaluation_config_views.undo_run_tags") as mock_undo:
            response = client.post(url)

        mock_undo.assert_not_called()
        assert response.status_code == 302
        assert response["Location"] == reverse(
            "evaluations:evaluation_results_home",
            args=[team_with_users.slug, config.pk, run.pk],
        )
        flashes = _flash_messages(response)
        assert any(level == "error" and "completed run" in msg for level, msg in flashes), flashes

    def test_undo_on_non_latest_full_run_is_rejected(self, client, team_with_users):
        """Only the latest completed FULL run is undoable; an older run is rejected."""
        user = team_with_users.members.filter(membership__groups__name="Super Admin").first()
        config = EvaluationConfigFactory.create(team=team_with_users)
        old_run = EvaluationRunFactory.create(
            team=team_with_users,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
            finished_at=timezone.now() - timedelta(hours=1),
        )
        EvaluationRunFactory.create(
            team=team_with_users,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
            finished_at=timezone.now(),
        )

        client.force_login(user)
        url = self._url(team_with_users, config, old_run)

        with patch("apps.evaluations.views.evaluation_config_views.undo_run_tags") as mock_undo:
            response = client.post(url)

        mock_undo.assert_not_called()
        assert response.status_code == 302
        flashes = _flash_messages(response)
        assert any(level == "error" for level, _ in flashes), flashes

    def test_undo_on_delta_run_is_rejected(self, client, team_with_users):
        """DELTA runs are never directly undoable; the view rejects them without calling undo."""
        config, _ = _setup_tagging_config(team_with_users)
        delta_run = EvaluationRunFactory.create(
            team=team_with_users,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.DELTA,
            finished_at=timezone.now(),
        )
        user = team_with_users.members.filter(membership__groups__name="Super Admin").first()
        client.force_login(user)
        url = self._url(team_with_users, config, delta_run)

        with patch("apps.evaluations.views.evaluation_config_views.undo_run_tags") as mock_undo:
            response = client.post(url)

        mock_undo.assert_not_called()
        assert response.status_code == 302
        flashes = _flash_messages(response)
        assert any(level == "error" and "latest evaluation run" in msg for level, msg in flashes), flashes

    def test_undo_latest_full_run_flips_live_state(self, client, team_with_users):
        """A successful POST runs the real undo: live tags revert to the prior FULL run and
        the undone run's tags_archived flips, exercising the view's success branch end-to-end."""
        base = timezone.now()
        config, evaluator = _setup_tagging_config(team_with_users)

        message = EvaluationMessageFactory.create(create_chat_messages=True)
        config.dataset.messages.add(message)

        full1 = _complete_run(config, evaluator, [(message, NEG)], EvaluationRunType.FULL, base)
        full2 = _complete_run(config, evaluator, [(message, POS)], EvaluationRunType.FULL, base + timedelta(minutes=10))

        # Live state after FULL2 re-tagged the message positive.
        assert _tags(message) == {"good"}

        user = team_with_users.members.filter(membership__groups__name="Super Admin").first()
        client.force_login(user)
        response = client.post(self._url(team_with_users, config, full2))

        assert response.status_code == 302
        flashes = _flash_messages(response)
        assert any(level == "success" for level, _ in flashes), flashes

        # Live tags reverted to the FULL1 epoch; archive flags reflect the restored state.
        assert _tags(message) == {"bad"}
        full1.refresh_from_db()
        full2.refresh_from_db()
        assert full2.tags_archived is True
        assert full1.tags_archived is False
