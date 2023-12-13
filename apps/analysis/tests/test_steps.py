import pytest

from apps.analysis.core import PipelineContext, Step, StepContext

from .demo_steps import Divide, FactorSay, Multiply


@pytest.mark.parametrize(
    "step, context, output",
    [
        (Multiply(params=FactorSay(factor=3)), StepContext[int](2), 6),
        (Divide(params=FactorSay(factor=2)), StepContext[int](10), 5),
    ],
)
def test_call(step: Step, context, output):
    step.initialize(PipelineContext())
    assert step(context).data == output
