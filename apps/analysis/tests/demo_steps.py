from typing import Any

from apps.analysis.core import BaseStep, Params, required


class FactorSay(Params):
    factor: required(int) = None
    say: str = None


class Multiply(BaseStep[int, int]):
    param_schema = FactorSay
    input_type = int
    output_type = int

    def run(self, params: FactorSay, data: int) -> tuple[int, dict]:
        if params.say:
            self.log.debug(params.say)
        return data * params.factor, {"some": "metadata"}


class Divide(BaseStep[int, int]):
    param_schema = FactorSay
    input_type = int
    output_type = int

    def run(self, params: FactorSay, data: int) -> tuple[int, dict]:
        if params.say:
            self.log.debug(params.say)
        return data / params.factor, {}


class SetFactor(BaseStep[Any, Any]):
    param_schema = FactorSay
    input_type = Any
    output_type = Any

    def run(self, params: FactorSay, data: Any) -> tuple[Any, dict]:
        self.pipeline_context.params["factor"] = params.factor
        return data, {}


class StrInt(BaseStep[str, int]):
    input_type = str
    output_type = int

    def run(self, params: Params, data: str) -> tuple[int, dict]:
        return int(data), {}


class IntStr(BaseStep[int, str]):
    input_type = int
    output_type = str

    def run(self, params: Params, data: int) -> tuple[str, dict]:
        return str(data), {}
