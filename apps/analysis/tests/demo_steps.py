from typing import Any

from apps.analysis.core import BaseStep, Params, StepContext, required


class FactorSay(Params):
    factor: required(int) = None
    say: str = None


class Multiply(BaseStep[int, int]):
    param_schema = FactorSay
    input_type = int
    output_type = int

    def run(self, params: FactorSay, context: StepContext[int]) -> StepContext[int]:
        if params.say:
            self.log.debug(params.say)
        return StepContext(context.get_data() * params.factor, metadata={"some": "metadata"})


class Divide(BaseStep[int, int]):
    param_schema = FactorSay
    input_type = int
    output_type = int

    def run(self, params: FactorSay, context: StepContext[int]) -> StepContext[int]:
        if params.say:
            self.log.debug(params.say)
        return StepContext(context.get_data() / params.factor)


class SetFactor(BaseStep[Any, Any]):
    param_schema = FactorSay
    input_type = Any
    output_type = Any

    def run(self, params: FactorSay, context: StepContext[Any]) -> StepContext[Any]:
        self.pipeline_context.params["factor"] = params.factor
        return StepContext(context.get_data())


class StrInt(BaseStep[str, int]):
    input_type = str
    output_type = int

    def run(self, params: Params, context: StepContext[str]) -> StepContext[int]:
        return StepContext(int(context.get_data()))


class IntStr(BaseStep[int, str]):
    input_type = int
    output_type = str

    def run(self, params: Params, context: StepContext[int]) -> StepContext[str]:
        return StepContext(str(context.get_data()))


class StrReverse(BaseStep[str, str]):
    input_type = str
    output_type = str

    def run(self, params: Params, context: StepContext[str]) -> StepContext[str]:
        return StepContext(context.get_data()[::-1])


class TokenizeStr(BaseStep[str, str]):
    input_type = str
    output_type = str

    def run(self, params: Params, context: StepContext[str]) -> list[StepContext[str]]:
        words = context.get_data().split()
        return [StepContext(word, name="token") for word in words]
