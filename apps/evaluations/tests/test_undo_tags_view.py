"""View tests for the undo_evaluation_run_tags endpoint."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.evaluations.models import EvaluationRunStatus, EvaluationRunType
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationRunFactory


def _flash_messages(response):
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


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
