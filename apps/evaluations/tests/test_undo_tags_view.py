"""View tests for the undo_evaluation_run_tags endpoint."""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationRunStatus, EvaluationRunType
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationRunFactory


@pytest.mark.django_db()
class TestUndoEvaluationRunTagsView:
    def _url(self, team_with_users, config, run):
        return reverse(
            "evaluations:evaluation_run_undo_tags",
            args=[team_with_users.slug, config.pk, run.pk],
        )

    def test_post_calls_undo_and_redirects_to_run_home(self, client, team_with_users):
        """A valid POST calls undo_run_tags and redirects to the run detail page."""
        user = team_with_users.members.filter(membership__groups__name="Super Admin").first()
        config = EvaluationConfigFactory.create(team=team_with_users)
        run = EvaluationRunFactory.create(
            team=team_with_users,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        )

        client.force_login(user)
        url = self._url(team_with_users, config, run)

        with patch("apps.evaluations.views.evaluation_config_views.undo_run_tags") as mock_undo:
            response = client.post(url)

        mock_undo.assert_called_once_with(run)
        assert response.status_code == 302
        assert response["Location"] == reverse(
            "evaluations:evaluation_results_home",
            args=[team_with_users.slug, config.pk, run.pk],
        )

    def test_undo_on_processing_run_is_rejected(self, client, team_with_users):
        """A POST to undo a PROCESSING run is rejected: undo is not called and user is redirected."""
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

    def test_get_returns_405(self, client, team_with_users):
        """GET is not allowed; view is POST-only."""
        user = team_with_users.members.first()
        config = EvaluationConfigFactory.create(team=team_with_users)
        run = EvaluationRunFactory.create(team=team_with_users, config=config)

        client.force_login(user)
        url = self._url(team_with_users, config, run)
        response = client.get(url)

        assert response.status_code == 405

    def test_unauthenticated_user_is_redirected(self, client, team_with_users):
        """Unauthenticated requests are redirected to login."""
        config = EvaluationConfigFactory.create(team=team_with_users)
        run = EvaluationRunFactory.create(team=team_with_users, config=config)

        url = self._url(team_with_users, config, run)
        response = client.post(url)

        assert response.status_code == 302
        assert "/login/" in response["Location"] or "/accounts/login/" in response["Location"]
