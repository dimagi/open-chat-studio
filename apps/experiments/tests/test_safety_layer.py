from unittest.mock import patch

from apps.chat.bots import TopicBot
from apps.experiments.models import SafetyLayer
from apps.service_providers.llm_service.runnables import SimpleExperimentRunnable
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import FakeLlm, FakeLlmService


def _fake_llm_service():
    fake_llm = FakeLlm(responses=["this is a test message"], token_counts=[30, 20, 10])
    return FakeLlmService(llm=fake_llm)


@patch("apps.chat.bots.SafetyBot.is_safe")
@patch("apps.chat.bots.create_conversation")
@patch("apps.chat.bots.notify_users_of_violation")
def test_violation_triggers_email(notify_users_of_violation_mock, create_conversation_mock, is_safe_mock, db):
    is_safe_mock.return_value = False
    experiment_session = ExperimentSessionFactory(experiment__safety_violation_notification_emails=["user@officer.com"])
    experiment = experiment_session.experiment

    experiment.get_llm_service = _fake_llm_service
    chain = SimpleExperimentRunnable(experiment=experiment, session=experiment_session)

    layer = SafetyLayer.objects.create(
        prompt_text="Is this message safe?", team=experiment.team, prompt_to_bot="Set the user right"
    )
    experiment.safety_layers.add(layer)

    bot = TopicBot(experiment_session)
    bot.chain = chain
    bot.process_input("It's my way or the highway!")
    notify_users_of_violation_mock.assert_called()
