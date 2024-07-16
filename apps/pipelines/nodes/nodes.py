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


class ExtractStructuredData(LLMResponse):
    __human_name__ = "Extract structured data"
    data_schema: str

    def _prompt_chain(self, participant_data):
        prompt = PromptTemplate.from_template(template="Current user data: {participant_data}: Conversations: {input}")
        return (
            {"input": RunnablePassthrough()}
            | RunnablePassthrough.assign(participant_data=RunnableLambda(lambda x: participant_data))
            | prompt
        )

    def extraction_chain(self, json_schema, participant_data):
        return self._prompt_chain(participant_data) | super().get_chat_model().with_structured_output(json_schema)

    def _process(self, state: PipelineState) -> RunnableLambda:
        json_schema = ExtractStructuredData.to_json_schema(json.loads(self.data_schema))
        participant_data = self.get_participant_data(state)
        input: str = state["messages"][-1]

        chain = self.extraction_chain(json_schema=json_schema, participant_data=participant_data)
        prompt_token_count = self._get_prompt_token_count(participant_data)

        chunks = self.chunk_messages(input, prompt_token_count=prompt_token_count)
        while len(chunks) > 1:
            extracted_data = []
            for message_chunk in chunks:
                output = chain.invoke(message_chunk, config=self._config)
                extracted_data.append(output)

            # Reducing step
            input = "\n".join([json.dumps(data) for data in extracted_data])
            new_chunks = self.chunk_messages(input, prompt_token_count=prompt_token_count)

            if len(new_chunks) >= len(chunks):
                # This will happen when the output tokens are more than the number of tokens per chunk.
                # This is unlikely to happen, but can when the amount of participant data is large enough
                # to drive the allowed chunk size down to a very small number.
                # Additionally, when the `data_schema` is also large, the model will output a large amount of
                # tokens per chunk, resulting in the output tokens being more than the chunk size. This will cause
                # more chunks and an infinite loop
                raise Exception("Stopping due to bloating chunks. Reduce the size of the data schema")
            chunks = new_chunks

        output = chain.invoke(chunks[0], config=self._config)
        return output

    def _get_prompt_token_count(self, participant_data: dict) -> int:
        llm = super().get_chat_model()
        prompt_chain = self._prompt_chain(participant_data)
        # If we invoke the chain with an empty input, we get the prompt without the conversation history, which
        # is what we want.
        output = prompt_chain.invoke(input="")
        return llm.get_num_tokens(output.text)

    def chunk_messages(self, input: str, prompt_token_count: int) -> list[str]:
        """Chunk messages using a splitter that considers the token count.
        Strategy:
        - chunk_size (in tokens) = The LLM's token limit - prompt_token_count
        - chunk_overlap is chosen to be 10%

        Note:
        Since we don't know the token limit of the LLM, we assume it to be 8192.
        """
        model_token_limit = 8192  # Get this from model metadata
        overlap_percentage = 0.1
        chunk_size_tokens = model_token_limit - prompt_token_count
        overlap_tokens = int(chunk_size_tokens * overlap_percentage)

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

    def get_participant_data(self, state: PipelineState):
        session = state["experiment_session"]
        return session.get_participant_data()

    @staticmethod
    def to_json_schema(data: dict):
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


class UpdateParticipantMemory(PipelineNode):
    """A simple component to merge the the input data into the participant's memory data. If key_name is specified
    the input data will be merged with `key_name` as the key name.
    """

    __human_name__ = "Update participant memory"
    key_name: str | None = None

    def _process(self, state: PipelineState) -> PipelineState:
        """Strategy:
        - If the input is a string or list, a `key_name` must be present to be valid JSON.
        - If `key_name` is present, we create a new dictionary with `input` as the value to `key_name`.
        - Merge this new dictionary with the current participant data, essentially overriding existing data
        """
        new_data = state["messages"][-1]
        session = state["experiment_session"]

        if isinstance(new_data, str) or isinstance(new_data, list):
            if not self.key_name:
                raise KeyError("A key is expected for a string or list value.")

        if self.key_name:
            new_data = {self.key_name: new_data}
        try:
            participant_data = ParticipantData.objects.for_experiment(session.experiment).get(
                participant=session.participant
            )

            participant_data.data = participant_data.data | new_data
            participant_data.save()
        except ParticipantData.DoesNotExist:
            ParticipantData.objects.create(
                participant=session.participant,
                content_object=session.experiment,
                team=session.team,
                data=new_data,
            )
