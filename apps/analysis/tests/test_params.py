from typing import Annotated

import pytest

from apps.analysis.core import Params, required


class ExampleParams(Params):
    simple: required(int) = None
    complex: required(dict | str) = None
    annotated: required(Annotated[int, "other metadata"]) = None


@pytest.mark.parametrize(
    "args, raises",
    [
        ({"simple": 1, "complex": "test", "annotated": 1}, False),
        ({"simple": 1, "complex": {"A": 1}, "annotated": 1}, False),
        ({"simple": 1, "complex": "test"}, True),
        ({"complex": "test", "annotated": 1}, True),
        ({"simple": 1, "annotated": 1}, True),
    ],
)
def test_params(args, raises):
    if raises:
        with pytest.raises(ValueError):
            ExampleParams(**args).check()
    else:
        ExampleParams(**args).check()


@pytest.mark.parametrize(
    "initial, params, expected",
    [
        (ExampleParams(), [{"simple": 1}], ExampleParams(simple=1)),
        (ExampleParams(), [{"simple": 1}, {"simple": 2}], ExampleParams(simple=2)),
        (ExampleParams(simple=2), [{"simple": 1}], ExampleParams(simple=2)),
    ],
)
def test_merge(initial, params, expected):
    assert initial.merge(*params) == expected
