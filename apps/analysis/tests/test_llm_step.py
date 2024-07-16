from unittest.mock import Mock

from apps.accounting.models import UsageType
from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.steps.processors import LlmCompletionStep, LlmCompletionStepParams
from apps.utils.langchain import build_fake_llm_service


def test_llm_step_usage_recording():
    params = LlmCompletionStepParams(prompt="test prompt {data}", llm_model="test model")
    step = LlmCompletionStep(params=params)
    service = build_fake_llm_service(["test response"])
    context = PipelineContext(run=Mock(group=Mock()))
    context.llm_service = service
    result = step.invoke(StepContext.initial(), context)
    assert result.data == "test response"
    assert service.usage_recorder.totals == {UsageType.INPUT_TOKENS: 1, UsageType.OUTPUT_TOKENS: 1}
