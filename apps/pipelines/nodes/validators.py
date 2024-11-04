from pydantic import BaseModel, Field

"""
validators: [{"name": "", params: {}}]

"""


class BaseValidator(BaseModel):
    name: str
    params: dict = Field(default_factory=dict)


class Required(BaseValidator):
    name: str = "required"


class GreaterThan(BaseValidator):
    name: str = "greater_than"
    value: int

    def model_post_init(self, *args, **kwargs):
        self.params["value"] = self.value


class LesserThan(BaseValidator):
    name: str = "lesser_than"
    value: int

    def model_post_init(self, *args, **kwargs):
        self.params["value"] = self.value


class ValidJson(BaseValidator):
    name: str = "valid_json"
