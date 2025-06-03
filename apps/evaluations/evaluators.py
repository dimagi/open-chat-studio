from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

from apps.evaluations.models import EvaluationMessage, EvaluationMessageTypeChoices
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service.main import LlmService
from apps.service_providers.models import LlmProviderModel
from apps.utils.langchain import dict_to_json_schema


class EvaluatorResult(BaseModel):
    # TODO: What to do?
    result: dict | None


class BaseEvaluator:
    def run(self, message: EvaluationMessage, message_type: EvaluationMessageTypeChoices) -> EvaluatorResult:
        raise NotImplementedError


class LLMResponseMixin(BaseModel):
    llm_provider_id: int
    llm_provider_model_id: int
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0, title="Temperature")

    def get_llm_service(self) -> LlmService:
        from apps.service_providers.models import LlmProvider

        try:
            provider = LlmProvider.objects.get(id=self.llm_provider_id)
            return provider.get_llm_service()
        except LlmProvider.DoesNotExist:
            raise  # TODO
        except ServiceProviderConfigError:
            raise  # TODO

    def get_llm_provider_model(self):
        try:
            return LlmProviderModel.objects.get(id=self.llm_provider_model_id)
        except LlmProviderModel.DoesNotExist:
            raise  # TODO

    def get_chat_model(self) -> BaseChatModel:
        return self.get_llm_service().get_chat_model(self.get_llm_provider_model().name, self.llm_temperature)


class LlmEvaluator(LLMResponseMixin, BaseEvaluator):
    # TODO: Use pipelines.nodes.nodes.LLMResponseMixin?
    prompt: str
    output_schema: dict

    def run(self, message: EvaluationMessage, message_type: EvaluationMessageTypeChoices) -> EvaluatorResult:
        if message_type == EvaluationMessageTypeChoices.ALL:
            input = f"Human: {message.human_message_content} \n AI: {message.ai_message_content}"
        elif message_type == EvaluationMessageTypeChoices.HUMAN:
            input = f"Human: {message.human_message_content}"
        elif message_type == EvaluationMessageTypeChoices.AI:
            input = f"AI: {message.ai_message_content}"

        output_schema = dict_to_json_schema(self.output_schema)
        llm = self.get_chat_model().with_structured_output(output_schema)
        prompt = PromptTemplate.from_template(self.prompt)
        chain = prompt | llm
        result = chain.invoke({"input": input, **message.context})
        return EvaluatorResult(result=result)
