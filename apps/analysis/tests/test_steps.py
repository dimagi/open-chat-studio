import pytest

from apps.analysis.core import Step, StepContext

from .demo_steps import Divide, FactorSay, Multiply


@pytest.mark.parametrize(
    "params, merge, raises",
    [
        (FactorSay(), {}, True),
        (FactorSay(say="hello"), {}, True),
        (FactorSay(factor=3), {}, False),
        (FactorSay(), {"say": "hello"}, True),
        (FactorSay(), {"factor": 3}, False),
        (FactorSay(factor=3), {"something": "else"}, False),
    ],
)
def test_required_param(params, merge, raises):
    merged = params.merge(merge)
    if raises:
        with pytest.raises(ValueError):
            merged.check()
    else:
        merged.check()


@pytest.mark.parametrize(
    "step, context, output",
    [
        (Multiply(params=FactorSay(factor=3)), StepContext[int](2), 6),
        (Divide(params=FactorSay(factor=2)), StepContext[int](10), 5),
    ],
)
def test_call(step: Step, context, output):
    assert step(context).data == output
