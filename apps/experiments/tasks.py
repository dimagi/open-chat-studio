import logging
import time

from celery.app import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone
from field_audit.models import AuditAction
from langchain_core.messages import AIMessage, HumanMessage
from taskbadger.celery import Task as TaskbadgerTask

from apps.channels.datamodels import Attachment, BaseMessage
from apps.chat.bots import create_conversation
from apps.chat.channels import WebChannel
from apps.experiments.export import filtered_export_to_csv, get_filtered_sessions
from apps.experiments.models import Experiment, ExperimentSession, PromptBuilderHistory, SourceMaterial
from apps.files.models import File
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.utils import current_team
from apps.users.models import CustomUser
from apps.utils.taskbadger import update_taskbadger_data

logger = logging.getLogger("ocs.experiments")


@shared_task(bind=True, base=TaskbadgerTask)
def async_export_chat(self, experiment_id: int, query_params: dict, time_zone) -> dict:
    experiment = Experiment.objects.get(id=experiment_id)
    filtered_sessions = get_filtered_sessions(experiment, query_params, time_zone)
    csv_in_memory = filtered_export_to_csv(experiment, filtered_sessions)
    filename = f"{experiment.name} Chat Export {timezone.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    file_obj = File.objects.create(
        name=filename,
        team=experiment.team,
        content_type="text/csv",
        file=ContentFile(csv_in_memory.getvalue().encode("utf-8"), name=filename),
    )
    return {"file_id": file_obj.id}


@shared_task(bind=True, base=TaskbadgerTask)
def async_create_experiment_version(
    self, experiment_id: int, version_description: str | None = None, make_default: bool = False
):
    try:
        experiment = Experiment.objects.prefetch_related("assistant", "pipeline").get(id=experiment_id)
        with current_team(experiment.team):
            experiment.create_new_version(version_description, make_default)
    finally:
        Experiment.objects.filter(id=experiment_id).update(create_version_task_id="", audit_action=AuditAction.AUDIT)


@shared_task(bind=True, base=TaskbadgerTask)
def get_response_for_webchat_task(
    self, experiment_session_id: int, experiment_id: int, message_text: str, attachments: list | None = None
) -> dict:
    response = {"response": None, "message_id": None, "error": None}
    experiment_session = ExperimentSession.objects.select_related("experiment", "experiment__team").get(
        id=experiment_session_id
    )
    try:
        experiment = Experiment.objects.get(id=experiment_id)
        web_channel = WebChannel(
            experiment,
            experiment_session.experiment_channel,
            experiment_session=experiment_session,
        )
        message_attachments = []
        if attachments:
            for file_entry in attachments:
                message_attachments.append(Attachment.model_validate(file_entry))

        message = BaseMessage(
            participant_id=experiment_session.participant.identifier,
            message_text=message_text,
            attachments=message_attachments,
        )
        update_taskbadger_data(self, web_channel, message)
        chat_message = web_channel.new_user_message(message)
        # In some instances, the ChatMessage is not saved to the DB e.g. errors
        # so add the response here as well as the message ID
        response["response"] = chat_message.content
        response["message_id"] = chat_message.id
    except Exception as e:
        logger.exception(e)
        response["error"] = str(e)
    finally:
        experiment_session.seed_task_id = ""
        experiment_session.save(update_fields=["seed_task_id"])

    return response


@shared_task
def get_prompt_builder_response_task(team_id: int, user_id, data_dict: dict) -> dict[str, str | int]:
    llm_service = LlmProvider.objects.get(id=data_dict["provider"]).get_llm_service()
    llm_provider_model = LlmProviderModel.objects.get(id=data_dict["providerModelId"])
    messages_history = data_dict["messages"]

    user = CustomUser.objects.get(id=user_id)

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
    source_material = SourceMaterial.objects.filter(id=data_dict["sourceMaterialID"]).first()
    source_material_material = source_material.material if source_material else ""

    llm = llm_service.get_chat_model(llm_provider_model.name, float(data_dict["temperature"]))
    conversation = create_conversation(data_dict["prompt"], source_material_material, llm)
    conversation.load_memory_from_messages(_convert_prompt_builder_history(messages_history))
    input_formatter = data_dict["inputFormatter"]
    if input_formatter:
        last_user_message = input_formatter.format(input=last_user_message)

    # Get the response from the bot using the last message from the user and return it
    answer, input_tokens, output_tokens = conversation.predict(last_user_message)

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
    history_event |= {"preview": answer, "time": timezone.now().time().strftime("%H:%M")}
    PromptBuilderHistory.objects.create(team_id=team_id, owner=user, history=history_event)
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
