import time
from datetime import datetime

import pymupdf4llm
from celery.app import shared_task
from langchain.schema import AIMessage, HumanMessage
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownTextSplitter
from pymupdf import Document as PyMuPDFDocument
from taskbadger.celery import Task as TaskbadgerTask

from apps.channels.datamodels import WebMessage
from apps.chat.bots import create_conversation
from apps.chat.channels import WebChannel
from apps.experiments.models import Experiment, ExperimentSession, PromptBuilderHistory, SourceMaterial
from apps.files.models import File
from apps.service_providers.models import LlmProvider
from apps.users.models import CustomUser
from apps.utils.taskbadger import update_taskbadger_data
from apps.vectordb.vectorstore import PGVector


@shared_task(bind=True, base=TaskbadgerTask)
def get_response_for_webchat_task(self, experiment_session_id: int, message_text: str) -> str:
    experiment_session = ExperimentSession.objects.get(id=experiment_session_id)
    message_handler = WebChannel(experiment_session.experiment_channel)
    message = WebMessage(chat_id=experiment_session.chat.id, message_text=message_text)
    update_taskbadger_data(self, message_handler, message)
    return message_handler.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask)
def store_rag_embedding(self, experiment_id: int, file_id: int) -> None:
    experiment = Experiment.objects.get(id=experiment_id)
    file = experiment.files.get(id=file_id)
    documents = load_rag_file(file)
    embeddings_model = experiment.get_llm_service().get_openai_embeddings()
    PGVector.from_documents(documents, embeddings_model, experiment)


def load_rag_file(file: File) -> list[Document]:
    """
    Loads a text file of any supported type (PDF, TXT, HTML) into Langchain.
    """

    if file.content_type == "application/pdf":
        doc = PyMuPDFDocument(stream=file.file.open(), filetype="pdf")
        md_text = pymupdf4llm.to_markdown(doc)
        splitter = MarkdownTextSplitter(chunk_size=1000, chunk_overlap=0)
        documents = splitter.create_documents([md_text])
    elif file.content_type.startswith("text"):
        with file.file.open() as f:
            metadata = {"source": file.name}
            doc = Document(page_content=f.read(), metadata=metadata)
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        documents = text_splitter.split_documents([doc])
    else:
        raise ValueError(f"Unsupported file type: {file.content_type}")

    return documents


@shared_task
def get_prompt_builder_response_task(team_id: int, user_id, data_dict: dict) -> dict[str, str | int]:
    llm_service = LlmProvider.objects.get(id=data_dict["provider"]).get_llm_service()
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

    llm = llm_service.get_chat_model(data_dict["model"], float(data_dict["temperature"]))
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
    history_event |= {"preview": answer, "time": datetime.now().time().strftime("%H:%M")}
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
