"""Tests for consent configuration visibility in the chatbot UI."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.utils.factories.experiment import ConsentFormFactory

ALL_STATES = {"disabled", "web-only", "enabled"}


@pytest.mark.django_db()
class TestConsentDetailIndicator:
    """Compact inline consent indicator on the chatbot detail page.

    Three states:
    - No consent form → disabled state
    - Form attached, conversational consent disabled → web-only state
    - Form attached, conversational consent enabled → enabled state
    """

    @pytest.mark.parametrize(
        ("consent_form", "conversational_consent_enabled", "expected_state"),
        [
            pytest.param(False, False, "disabled", id="no-consent-form"),
            pytest.param(True, False, "web-only", id="form-attached-conversational-disabled"),
            pytest.param(True, True, "enabled", id="form-attached-conversational-enabled"),
        ],
    )
    def test_consent_state_indicator(
        self, client, team_with_users, consent_form, conversational_consent_enabled, expected_state
    ):
        team = team_with_users
        user = team.members.first()
        user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(user)

        pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
        experiment = Experiment.objects.create(
            name="Test Bot",
            owner=user,
            team=team,
            pipeline=pipeline,
            consent_form=ConsentFormFactory.create(team=team) if consent_form else None,
            conversational_consent_enabled=conversational_consent_enabled,
        )

        url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
        response = client.get(url)
        content = response.content.decode()

        assert response.status_code == 200
        assert f'data-consent-state="{expected_state}"' in content
        for other_state in ALL_STATES - {expected_state}:
            assert f'data-consent-state="{other_state}"' not in content
