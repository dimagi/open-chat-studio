"""Tests for consent configuration visibility in the chatbot UI."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.utils.factories.experiment import ConsentFormFactory


@pytest.mark.django_db()
class TestConsentDetailIndicator:
    """Compact inline consent indicator on the chatbot detail page.

    Three states:
    - No consent form → disabled state (❌)
    - Form attached, conversational consent disabled → web-only state (⚠️)
    - Form attached, conversational consent enabled → enabled state (✔️)
    """

    def _get_response(self, client, team, experiment):
        url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
        return client.get(url)

    def _login_user_with_view_perm(self, client, team):
        user = team.members.first()
        user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(user)
        return user

    def test_disabled_state_when_no_consent_form(self, client, team_with_users):
        team = team_with_users
        user = self._login_user_with_view_perm(client, team)
        pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
        experiment = Experiment.objects.create(
            name="No Consent Bot", owner=user, team=team, pipeline=pipeline, consent_form=None
        )

        response = self._get_response(client, team, experiment)
        content = response.content.decode()

        assert response.status_code == 200
        assert 'data-consent-state="disabled"' in content
        assert 'data-consent-state="web-only"' not in content
        assert 'data-consent-state="enabled"' not in content

    def test_web_only_state_when_form_attached_but_conversational_consent_disabled(self, client, team_with_users):
        team = team_with_users
        user = self._login_user_with_view_perm(client, team)
        pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
        consent_form = ConsentFormFactory.create(team=team)
        experiment = Experiment.objects.create(
            name="Form Only Bot",
            owner=user,
            team=team,
            pipeline=pipeline,
            consent_form=consent_form,
            conversational_consent_enabled=False,
        )

        response = self._get_response(client, team, experiment)
        content = response.content.decode()

        assert response.status_code == 200
        assert 'data-consent-state="web-only"' in content
        assert 'data-consent-state="disabled"' not in content
        assert 'data-consent-state="enabled"' not in content

    def test_enabled_state_when_consent_fully_configured(self, client, team_with_users):
        team = team_with_users
        user = self._login_user_with_view_perm(client, team)
        pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
        consent_form = ConsentFormFactory.create(team=team)
        experiment = Experiment.objects.create(
            name="Full Consent Bot",
            owner=user,
            team=team,
            pipeline=pipeline,
            consent_form=consent_form,
            conversational_consent_enabled=True,
        )

        response = self._get_response(client, team, experiment)
        content = response.content.decode()

        assert response.status_code == 200
        assert 'data-consent-state="enabled"' in content
        assert 'data-consent-state="disabled"' not in content
        assert 'data-consent-state="web-only"' not in content
