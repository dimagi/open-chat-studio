import pytest
from django.test import RequestFactory

from apps.chatbots.forms import ChatbotForm
from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.teams.utils import set_current_team


@pytest.mark.django_db()
def test_chatbot_form_valid_data(team_with_users):
    team = team_with_users
    user = team.members.first()
    request = RequestFactory().get("/")
    request.team = team
    request.user = user
    set_current_team(team)
    form_data = {
        "name": "Test Chatbot",
        "description": "A chatbot for testing",
    }

    form = ChatbotForm(request, data=form_data)

    assert form.is_valid()
    experiment = form.save()
    assert Experiment.objects.filter(name="Test Chatbot", team=team).exists()
    assert experiment.pipeline is not None
    assert experiment.owner == user


@pytest.mark.django_db()
def test_chatbot_form_missing_name(team_with_users):
    team = team_with_users
    user = team.members.first()
    request = RequestFactory().get("/")
    request.team = team
    request.user = user

    form_data = {
        "name": "",  # Missing name
        "description": "A chatbot without a name",
    }

    form = ChatbotForm(request, data=form_data)

    assert not form.is_valid()
    assert "name" in form.errors


@pytest.mark.django_db()
def test_chatbot_form_pipeline_creation(team_with_users):
    team = team_with_users
    user = team.members.first()
    request = RequestFactory().get("/")
    request.user = user
    request.team = team
    form_data = {
        "name": "Chatbot with Pipeline",
        "description": "Testing pipeline creation",
    }
    set_current_team(team)
    form = ChatbotForm(request, data=form_data)

    assert form.is_valid()
    experiment = form.save()
    assert Pipeline.objects.filter(name="Chatbot with Pipeline", team=team).exists()
    assert experiment.pipeline is not None
