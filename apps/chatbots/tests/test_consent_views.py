"""Tests for consent configuration visibility in the chatbot UI."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.utils.factories.experiment import ConsentFormFactory


@pytest.mark.django_db()
class TestConsentWarnings:
    """Warnings on the chatbot detail page when consent is not fully configured.

    Three states drive the UI:
    - No consent form → prominent warning banner
    - Form attached, conversational consent disabled → softer info banner
    - Form attached, conversational consent enabled → no banner
    """

    def _get_response(self, client, team, experiment):
        url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
        return client.get(url)

    def _login_user_with_view_perm(self, client, team):
        user = team.members.first()
        user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(user)
        return user

    def test_warning_banner_shown_when_no_consent_form(self, client, team_with_users):
        team = team_with_users
        user = self._login_user_with_view_perm(client, team)
        pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
        experiment = Experiment.objects.create(
            name="No Consent Bot", owner=user, team=team, pipeline=pipeline, consent_form=None
        )

        response = self._get_response(client, team, experiment)
        content = response.content.decode()

        assert response.status_code == 200
        assert "chatbot-consent-missing-banner" in content
        assert "No consent form configured" in content
        assert "chatbot-consent-disabled-banner" not in content

    def test_info_banner_shown_when_consent_form_attached_but_disabled(self, client, team_with_users):
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
        assert "chatbot-consent-disabled-banner" in content
        assert "Conversational consent is disabled" in content
        assert "chatbot-consent-missing-banner" not in content

    def test_no_banner_when_consent_fully_configured(self, client, team_with_users):
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
        assert "chatbot-consent-missing-banner" not in content
        assert "chatbot-consent-disabled-banner" not in content


@pytest.mark.django_db()
def test_chatbot_list_shows_consent_status_column(client, team_with_users):
    """The chatbot list page must surface consent state per row so missing/partial setup is visible at a glance."""
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
    client.force_login(user)

    consent_form = ConsentFormFactory.create(team=team)
    Experiment.objects.create(
        name="No Consent Bot",
        owner=user,
        team=team,
        pipeline=Pipeline.objects.create(team=team, data={"nodes": [], "edges": []}),
        consent_form=None,
    )
    Experiment.objects.create(
        name="Form Only Bot",
        owner=user,
        team=team,
        pipeline=Pipeline.objects.create(team=team, data={"nodes": [], "edges": []}),
        consent_form=consent_form,
        conversational_consent_enabled=False,
    )
    Experiment.objects.create(
        name="Full Consent Bot",
        owner=user,
        team=team,
        pipeline=Pipeline.objects.create(team=team, data={"nodes": [], "edges": []}),
        consent_form=consent_form,
        conversational_consent_enabled=True,
    )

    url = reverse("chatbots:table", args=[team.slug])
    response = client.get(url)
    content = response.content.decode()

    assert response.status_code == 200
    # Column header
    assert "Consent" in content
    # Each badge variant should appear with its consent-specific label and tooltip,
    # so the assertions match the consent indicator and not unrelated badges.
    assert "badge-warning" in content
    assert "badge-info" in content
    assert "badge-success" in content
    assert "No consent form configured" in content
    assert "conversational consent is disabled" in content
    assert "conversational consent is enabled" in content
