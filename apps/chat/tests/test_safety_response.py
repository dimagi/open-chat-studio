from unittest.mock import patch

import pytest

from apps.chat.bots import TopicBot
from apps.experiments.models import SafetyLayer
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm_service


@pytest.mark.django_db()
@patch("apps.chat.bots.SafetyBot.is_safe")
def test_safety_response(is_safe_mock):
    is_safe_mock.return_value = False

    session = ExperimentSessionFactory()
    experiment = session.experiment
    layer = SafetyLayer.objects.create(
        prompt_text="Is this message safe?", team=experiment.team, prompt_to_bot="Unsafe reply"
    )
    experiment.safety_layers.add(layer)

    expected = "Sorry I can't help with that."
    bot = TopicBot(session)
    with patch.object(TopicBot, "_get_safe_response", wraps=bot._get_safe_response) as mock_get_safe_response:
        with mock_experiment_llm_service(responses=[expected]):
            response = bot.process_input("It's my way or the highway!")

    mock_get_safe_response.assert_called()
    assert response == expected
