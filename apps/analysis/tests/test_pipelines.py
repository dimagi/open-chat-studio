import pytest

from apps.analysis.core import NoParams, Pipeline, PipelineContext, StepContext, StepError

from .demo_steps import Divide, FactorSay, IntStr, Multiply, SetFactor, StrInt


@pytest.mark.parametrize(
    "pipeline, pipeline_context, context, output",
    [
        (
            Pipeline([Multiply(params=FactorSay(factor=3)), Divide(params=FactorSay(factor=2))]),
            PipelineContext(None),
            StepContext[int](10),
            15,
        ),
        # params passed from previous step
        (
            Pipeline([SetFactor(params=FactorSay(factor=3)), Multiply()]),
            PipelineContext(None),
            StepContext[int](2),
            6,
        ),
        (
            Pipeline(
                [
                    Multiply(),  # x 2 (param from pipeline context)
                    Multiply(params=FactorSay(factor=3)),  # x 3  (param from params)
                    SetFactor(params=FactorSay(factor=4)),  # set params for next step
                    Divide(),  # / 4  (param from previous step)
                ]
            ),
            PipelineContext(None, params={"factor": 2}),
            StepContext[int](2),
            3,
        ),
    ],
)
def test_pipeline(pipeline: Pipeline, pipeline_context, context, output):
    assert pipeline.run(pipeline_context, context).data == output


@pytest.mark.parametrize(
    "chain, valid",
    [
        ([Divide, Multiply], True),
        ([Multiply, Divide], True),
        ([StrInt, IntStr], True),
        ([StrInt, StrInt], False),
        ([IntStr, StrInt], True),
        ([IntStr, IntStr], False),
    ],
)
def test_validate_types(chain, valid):
    chain = [c(params=NoParams()) for c in chain]
    if valid:
        Pipeline(chain)
    else:
        with pytest.raises(StepError):
            Pipeline(chain)
