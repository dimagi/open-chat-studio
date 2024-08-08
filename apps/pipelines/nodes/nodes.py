import json

import tiktoken
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import Field, create_model

from apps.experiments.models import ParticipantData
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.nodes.base import PipelineNode, PipelineState
from apps.pipelines.nodes.types import LlmModel, LlmProviderId, LlmTemperature, PipelineJinjaTemplate
from apps.pipelines.tasks import send_email_from_pipeline
from apps.service_providers.exceptions import ServiceProviderConfigError


class RenderTemplate(PipelineNode):
    __human_name__ = "Render a template"
    template_string: PipelineJinjaTemplate

    def _process(self, state: PipelineState) -> PipelineState:
        input = state["messages"][-1]

        env = SandboxedEnvironment()
        try:
            if isinstance(input, BaseMessage):
                content = json.loads(input.content)
            elif isinstance(input, dict):
                content = input
            else:
                content = json.loads(input)
        except json.JSONDecodeError:
            # As a last resort, just set the all the variables in the template to the input
            content = {var: input for var in meta.find_undeclared_variables(env.parse(self.template_string))}
        template = SandboxedEnvironment().from_string(self.template_string)
        return template.render(content)


class LLMResponse(PipelineNode):
    __human_name__ = "LLM response"

    llm_provider_id: LlmProviderId
    llm_model: LlmModel
    llm_temperature: LlmTemperature = 1.0

    def _process(self, state: PipelineState) -> PipelineState:
        llm = self.get_chat_model()
        output = llm.invoke(state["messages"][-1], config=self._config)
        return output.content

    def get_chat_model(self):
        from apps.service_providers.models import LlmProvider

        provider = LlmProvider.objects.get(id=self.llm_provider_id)
        try:
            service = provider.get_llm_service()
            return service.get_chat_model(self.llm_model, self.llm_temperature)
        except LlmProvider.DoesNotExist:
            raise PipelineNodeBuildError(f"LLM provider with id {self.llm_provider_id} does not exist")
        except ServiceProviderConfigError as e:
            raise PipelineNodeBuildError("There was an issue configuring the LLM service provider") from e


class CreateReport(LLMResponse):
    __human_name__ = "Create a report"

    prompt: str = (
        "Make a summary of the following text: {input}. "
        "Output it as JSON with a single key called 'summary' with the summary."
    )

    def _process(self, state: PipelineState) -> PipelineState:
        chain = PromptTemplate.from_template(template=self.prompt) | super().get_chat_model()
        output = chain.invoke(state["messages"][-1], config=self._config)
        return output.content


class SendEmail(PipelineNode):
    __human_name__ = "Send an email"
    recipient_list: str
    subject: str

    def _process(self, state: PipelineState) -> PipelineState:
        send_email_from_pipeline.delay(
            recipient_list=self.recipient_list.split(","), subject=self.subject, message=state["messages"][-1]
        )


class Passthrough(PipelineNode):
    __human_name__ = "Do Nothing"

    def _process(self, state: PipelineState) -> PipelineState:
        input = state["messages"][-1]
        self.logger.debug(f"Returning input: '{input}' without modification", input=input, output=input)
        return input


class ExtractStructuredDataNodeMixin:
    def _prompt_chain(self, reference_data):
        template = (
            "Extract user data using the current user data and conversation history as reference. Use JSON output."
            "\nCurrent user data:"
            "\n{reference_data}"
            "\nConversation history:"
            "\n{input}"
            "The conversation history should carry more weight in the outcome. It can change the user's current data"
        )
        prompt = PromptTemplate.from_template(template=template)
        return (
            {"input": RunnablePassthrough()}
            | RunnablePassthrough.assign(reference_data=RunnableLambda(lambda x: reference_data))
            | prompt
        )

    def extraction_chain(self, json_schema, reference_data):
        return self._prompt_chain(reference_data) | super().get_chat_model().with_structured_output(json_schema)

    def _process(self, state: PipelineState) -> RunnableLambda:
        json_schema = self.to_json_schema(json.loads(self.data_schema))
        input: str = state["messages"][-1]
        reference_data = self.get_reference_data(state)
        prompt_token_count = self._get_prompt_token_count(reference_data, json_schema)
        message_chunks = self.chunk_messages(input, prompt_token_count=prompt_token_count)

        new_reference_data = reference_data
        for idx, message_chunk in enumerate(message_chunks, start=1):
            chain = self.extraction_chain(json_schema=json_schema, reference_data=new_reference_data)
            output = chain.invoke(message_chunk, config=self._config)
            self.logger.info(
                f"Chunk {idx}",
                input=f"\nReference data:\n{new_reference_data}\nChunk data:\n{message_chunk}\n\n",
                output=f"\nExtracted data:\n{output}",
            )
            new_reference_data = self.update_reference_data(output, reference_data)

        self.post_extraction_hook(new_reference_data, state)
        return json.dumps(new_reference_data)

    def post_extraction_hook(self, output, state):
        pass

    def get_reference_data(self, state):
        return ""

    def update_reference_data(self, new_data: dict, reference_data: dict) -> dict:
        return new_data

    def _get_prompt_token_count(self, reference_data: dict | str, json_schema: dict) -> int:
        llm = super().get_chat_model()
        prompt_chain = self._prompt_chain(reference_data)
        # If we invoke the chain with an empty input, we get the prompt without the conversation history, which
        # is what we want.
        output = prompt_chain.invoke(input="")
        json_schema_tokens = llm.get_num_tokens(json.dumps(json_schema))
        return llm.get_num_tokens(output.text) + json_schema_tokens

    def chunk_messages(self, input: str, prompt_token_count: int) -> list[str]:
        """Chunk messages using a splitter that considers the token count.
        Strategy:
        - chunk_size (in tokens) = The LLM's token limit - prompt_token_count
        - chunk_overlap is chosen to be 20%

        Note:
        Since we don't know the token limit of the LLM, we assume it to be 8192.
        """
        model_token_limit = 8192  # Get this from model metadata
        overlap_percentage = 0.2
        chunk_size_tokens = model_token_limit - prompt_token_count
        overlap_tokens = int(chunk_size_tokens * overlap_percentage)
        self.logger.debug(f"Chunksize in tokens: {chunk_size_tokens} with {overlap_tokens} tokens overlap")

        try:
            encoding = tiktoken.encoding_for_model(self.llm_model)
            encoding_name = encoding.name
        except KeyError:
            # The same encoder we use for llm.get_num_tokens_from_messages
            encoding_name = "gpt2"

        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=encoding_name,
            chunk_size=chunk_size_tokens,
            chunk_overlap=overlap_tokens,
        )

        return text_splitter.split_text(input)

    def to_json_schema(self, data: dict):
        """Converts a dictionary to a JSON schema by first converting it to a Pydantic object and dumping it again.
        The input should be in the format {"key": "description", "key2": [{"key": "description"}]}

        Nested objects are not supported at the moment

        Input example 1:
        {"name": "the user's name", "surname": "the user's surname"}

        Input example 2:
        {"name": "the user's name", "pets": [{"name": "the pet's name": "type": "the type of animal"}]}

        """

        def _create_model_from_data(value_data, model_name: str):
            pydantic_schema = {}
            for key, value in value_data.items():
                if isinstance(value, str):
                    pydantic_schema[key] = (str | None, Field(description=value))
                elif isinstance(value, list):
                    model = _create_model_from_data(value[0], key.capitalize())
                    pydantic_schema[key] = (list[model], Field(description=f"A list of {key}"))
            return create_model(model_name, **pydantic_schema)

        Model = _create_model_from_data(data, "CustomModel")
        schema = Model.model_json_schema()
        # The schema needs a description in order to comply with function calling APIs
        schema["description"] = ""
        return schema


class ExtractStructuredData(ExtractStructuredDataNodeMixin, LLMResponse):
    __human_name__ = "Extract Structured Data"
    data_schema: str


class ExtractParticipantData(ExtractStructuredDataNodeMixin, LLMResponse):
    __human_name__ = "Extract Participant Data"
    data_schema: str
    key_name: str | None = None

    def get_reference_data(self, state) -> dict:
        """Returns the participant data as reference. If there is a `key_name`, the value in the participant data
        corresponding to that key will be returned insteadg
        """
        session = state["experiment_session"]
        participant_data = (
            ParticipantData.objects.for_experiment(session.experiment).filter(participant=session.participant).first()
        )
        if not participant_data:
            return ""

        data = participant_data.data
        if self.key_name:
            # string, list or dict
            return data.get(self.key_name, "")
        return data

    def update_reference_data(self, new_data: dict, reference_data: dict | list | str) -> dict:
        if isinstance(reference_data, dict):
            # new_data may be a subset, superset or wholly different set of keys than the reference_data, so merge
            return reference_data | new_data

        # if reference data is a string or list, we cannot merge, so let's override
        return new_data

    def post_extraction_hook(self, output, state):
        session = state["experiment_session"]
        if self.key_name:
            output = {self.key_name: output}

        try:
            participant_data = ParticipantData.objects.for_experiment(session.experiment).get(
                participant=session.participant
            )
            participant_data.data = participant_data.data | output
            participant_data.save()
        except ParticipantData.DoesNotExist:
            ParticipantData.objects.create(
                participant=session.participant,
                content_object=session.experiment,
                team=session.team,
                data=output,
            )
