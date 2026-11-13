import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from apps.analysis.forms import TranscriptAnalysisForm
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.mark.django_db()
def test_a_jev_provider_is_neither_offered_nor_accepted(experiment):
    """Analysis writes its findings as text, and Jev only classifies."""
    team = experiment.team
    jev = LlmProviderFactory.create(team=team, type="jev")
    jev_model = LlmProviderModelFactory.create(team=team, type="jev", name="jev-latest")
    request = RequestFactory().get("/")
    request.session = SessionStore()

    form = TranscriptAnalysisForm(request=request, experiment=experiment, team=team)

    assert not [value for value, _label in form.fields["provider_model"].choices if value.startswith(f"{jev.id}:")]
    with pytest.raises(ValidationError, match="Invalid LLM provider selected"):
        form._validate_provider_model(f"{jev.id}:{jev_model.id}")
