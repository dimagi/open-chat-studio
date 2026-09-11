"""Tests for EvaluationResultHome: the run-details disclosure hides the Generation
chatbot field for session-mode runs, where generation never applies.
"""

import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationRunStatus
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationDatasetFactory, EvaluationRunFactory


@pytest.mark.django_db()
class TestGenerationChatbotVisibility:
    def test_shown_for_message_mode_run(self, client, team_with_users):
        dataset = EvaluationDatasetFactory.create(team=team_with_users, evaluation_mode="message")
        config = EvaluationConfigFactory.create(team=team_with_users, dataset=dataset)
        run = EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert response.context["is_session_mode"] is False
        assert "Generation chatbot" in response.content.decode()

    def test_hidden_for_session_mode_run(self, client, team_with_users):
        dataset = EvaluationDatasetFactory.create(team=team_with_users, evaluation_mode="session")
        config = EvaluationConfigFactory.create(team=team_with_users, dataset=dataset)
        run = EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert response.context["is_session_mode"] is True
        assert "Generation chatbot" not in response.content.decode()
