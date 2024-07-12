import time
from datetime import datetime

from celery.app import shared_task
from langchain.schema import AIMessage, HumanMessage
from taskbadger.celery import Task as TaskbadgerTask

from apps.accounting.models import UsageType
from apps.channels.datamodels import BaseMessage
from apps.chat.bots import create_conversation
from apps.chat.channels import WebChannel
from apps.experiments.models import ExperimentSession, PromptBuilderHistory, SourceMaterial
from apps.service_providers.models import LlmProvider
from apps.users.models import CustomUser
from apps.utils.taskbadger import update_taskbadger_data


@shared_task(bind=True, base=TaskbadgerTask)
def get_response_for_webchat_task(self, experiment_session_id: int, message_text: str) -> str:
    experiment_session = ExperimentSession.objects.get(id=experiment_session_id)
    web_channel = WebChannel(
        experiment_channel=experiment_session.experiment_channel, experiment_session=experiment_session
    )
    message = BaseMessage(participant_id=experiment_session.participant.identifier, message_text=message_text)
    update_taskbadger_data(self, web_channel, message)
    return web_channel.new_user_message(message)


@shared_task
def get_prompt_builder_response_task(team_id: int, user_id, data_dict: dict) -> dict[str, str | int]:
    llm_service = LlmProvider.objects.get(team_id=team_id, id=data_dict["provider"]).get_llm_service()
    user = CustomUser.objects.get(id=user_id)
    return get_prompt_builder_response(llm_service, team_id, user, data_dict)


def get_prompt_builder_response(llm_service, team_id: int, user, data_dict: dict) -> dict[str, str | int]:
    messages_history = data_dict["messages"]

    # Get the last message from the user
    # If the most recent message was from the AI, then
    # we will send a blank msg. TODO: Do something smarter here.
    last_user_message = ""
    last_user_object = None
    if messages_history and messages_history[-1]["author"] == "User":
        # Do not send the last message from the user in the messages_history
        last_user_object = messages_history.pop()
        last_user_message = last_user_object["message"]

    # Fetch source material
    source_material_material = ""
    if source_material_id := data_dict.get("sourceMaterialID"):
        source_material = SourceMaterial.objects.filter(id=source_material_id).first()
        source_material_material = source_material.material if source_material else ""

    llm = llm_service.get_chat_model(data_dict["model"], float(data_dict["temperature"]))
    conversation = create_conversation(data_dict["prompt"], source_material_material, llm)
    conversation.load_memory_from_messages(_convert_prompt_builder_history(messages_history))
    input_formatter = data_dict["inputFormatter"]
    if input_formatter:
        last_user_message = input_formatter.format(input=last_user_message)

    # Get the response from the bot using the last message from the user and return it
    history = PromptBuilderHistory.objects.create(team_id=team_id, owner=user, history=[])
    with llm_service.record_usage(history) as usage:
        answer = conversation.predict(last_user_message)

    input_tokens = usage.totals.get(UsageType.INPUT_TOKENS, 0)
    output_tokens = usage.totals.get(UsageType.OUTPUT_TOKENS, 0)

    # Push the user message back into the message list now that the bot response has arrived
    if last_user_object:
        messages_history.append(last_user_object)

    # Create a history event. This isn't a deep copy this dictionary, but I think that's fine.
    history_event = data_dict
    history_event["messages"].append(
        {
            "author": "Assistant",
            "message": answer,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # The id field is used on the UI side. It just has to be unique
            # The reason we're doing this is to mimic JS's Date.now() output,
            # which we use when we add new messages on the UI side.
            # It doens't acutally matter as long as the ID doesn't conflict.
            # I prepended the character s to denote server-side and so that in
            # the infinitely small case that timezones mean the server and client
            # create some overlap it won't actually clash.
            "id": f"s{int(time.time() * 1000)}",
        }
    )
    history_event |= {"preview": answer, "time": datetime.now().time().strftime("%H:%M")}
    history.history = history_event
    history.save()
    return {"message": answer, "input_tokens": input_tokens, "output_tokens": output_tokens}


def _convert_prompt_builder_history(messages_history):
    history = []
    for message in messages_history:
        if "message" not in message:
            continue
        if message["author"] == "User":
            history.append(HumanMessage(content=message["message"]))
        elif message["author"] == "Assistant":
            history.append(AIMessage(content=message["message"]))
    return history
