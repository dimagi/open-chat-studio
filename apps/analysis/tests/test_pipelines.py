import pytest

from apps.analysis.core import NoParams, Pipeline, PipelineContext, StepContext

from ..exceptions import StepError
from .demo_steps import Divide, FactorSay, IntStr, Multiply, Reverse, SetFactor, SplitLines, StrInt, TokenizeStr


@pytest.mark.parametrize(
    ("pipeline", "pipeline_context", "context", "output"),
    [
        pytest.param(
            Pipeline([Multiply(params=FactorSay(factor=3)), Divide(params=FactorSay(factor=2))]),
            PipelineContext(),
            StepContext[int](10),
            15,
            id="simple pipeline",
        ),
        pytest.param(
            Pipeline([SetFactor(params=FactorSay(factor=3)), Multiply()]),
            PipelineContext(),
            StepContext[int](2),
            6,
            id="set params",
        ),
        pytest.param(
            Pipeline(
                [
                    Multiply(),  # x 2 (param from pipeline context)
                    Multiply(params=FactorSay(factor=3)),  # x 3  (param from params)
                    SetFactor(params=FactorSay(factor=4)),  # set params for next step
                    Divide(),  # / 4  (param from previous step)
                ]
            ),
            PipelineContext(params={"factor": 2}),
            StepContext[int](2),
            3,
            id="diverse params test",
        ),
        pytest.param(
            Pipeline(
                [
                    SplitLines(),
                    TokenizeStr(),
                    Reverse(),
                ]
            ),
            PipelineContext(),
            StepContext[int]("This is a\nmultiline string\nwith 3 lines."),
            [["sihT", "si", "a"], ["enilitlum", "gnirts"], ["htiw", "3", ".senil"]],
            id="list outputs",
        ),
        pytest.param(
            Pipeline([Multiply(step_id="1"), Multiply(step_id="2")]),
            PipelineContext(params={"Multiply:1": {"factor": 2}, "Multiply:2": {"factor": 3}}),
            StepContext[int](2),
            12,
            id="duplicate steps",
        ),
    ],
)
def test_pipeline(pipeline: Pipeline, pipeline_context, context, output):
    def _unwrap_result(res):
        if isinstance(res, list):
            return [_unwrap_result(r) for r in res]
        else:
            return res.get_data()

    result = pipeline.run(pipeline_context, context)
    assert _unwrap_result(result) == output


@pytest.mark.parametrize(
    ("params", "context", "expected"),
    [
        (None, {"factor": 2}, FactorSay(factor=2)),
        (FactorSay(say="hi"), {"factor": 2}, FactorSay(factor=2, say="hi")),
        (None, {"factor": 2, "say": "hi", "Divide": {"factor": 3}}, FactorSay(factor=3, say="hi")),
        (FactorSay(factor=1), {"factor": 2, "Divide": {"factor": 3}}, FactorSay(factor=1)),
    ],
)
def test_params(params, context, expected):
    step = Divide(params=params)
    step.invoke(StepContext.initial(2), PipelineContext(params=context))
    assert step.params == expected


@pytest.mark.parametrize(
    ("chain", "valid"),
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
