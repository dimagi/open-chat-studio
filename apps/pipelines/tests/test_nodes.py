import contextlib

import pytest
from pydantic import BaseModel, TypeAdapter
from pydantic_core import ValidationError

from apps.pipelines.nodes.nodes import (
    AnthropicWebSearchToolConfig,
    LLMResponseWithPrompt,
    OptionalInt,
    SendEmail,
    StructuredDataSchemaValidatorMixin,
)


class TestStructuredDataSchemaValidatorMixin:
    class DummyModel(BaseModel, StructuredDataSchemaValidatorMixin):
        data_schema: str

    def test_valid_schema(self):
        valid_schema = '{"name": "the name of the user"}'
        model = self.DummyModel(data_schema=valid_schema)
        assert model.data_schema == valid_schema

    @pytest.mark.parametrize("schema", ['{"name": "the name of the user"', "{}", "[]"])
    def test_invalid_schema(self, schema):
        with pytest.raises(ValidationError, match="Invalid schema"):
            self.DummyModel(data_schema=schema)


class TestSendEmailInputValidation:
    @pytest.mark.parametrize(
        "recipient_list",
        [
            "test@example.com",
            "test@example.com,another@example.com",
            "test@example.com,another@example.com,yetanother@example.com",
        ],
    )
    def test_valid_recipient_list(self, recipient_list):
        model = SendEmail(
            node_id="test", django_node=None, name="email", recipient_list=recipient_list, subject="Test Subject"
        )
        assert model.recipient_list == recipient_list

    @pytest.mark.parametrize(
        "recipient_list",
        [
            "",
            "invalid-email",
            "test@example.com,invalid-email",
            "test@example.com,another@example.com,invalid-email",
        ],
    )
    def test_invalid_recipient_list(self, recipient_list):
        with pytest.raises(ValidationError, match="Invalid list of emails addresses"):
            SendEmail(name="email", recipient_list=recipient_list, subject="Test Subject")


def test_optional_int_type():
    ta = TypeAdapter(OptionalInt)
    assert ta.validate_python(1) == 1
    assert ta.validate_python(None) is None
    assert ta.validate_python("") is None

    with pytest.raises(ValidationError):
        ta.validate_python(1.2)

    with pytest.raises(ValidationError):
        ta.validate_python("test")


@pytest.mark.parametrize(
    ("allowed_domains", "blocked_domains", "error_expected"),
    [
        ([], [], False),
        ([""], [""], False),
        (["invalid-domain"], [], True),
        ([], ["invalid-domain"], True),
        (["test.com"], [], False),
        ([], ["test.com"], False),
        (["test.com", "example.com"], [], False),
        (["test.com", "@example.com"], [], True),
        ([], ["test.com", "example.com"], False),
        ([], ["test.@com", "example.com"], True),
    ],
)
def test_tool_config(allowed_domains, blocked_domains, error_expected):
    raw_config = {"allowed_domains": allowed_domains, "blocked_domains": blocked_domains}
    context = pytest.raises(ValidationError) if error_expected else contextlib.nullcontext()
    with context:
        node = LLMResponseWithPrompt.model_validate(
            {
                "node_id": "123",
                "django_node": None,
                "name": "LLMResponseWithPrompt-aAgkv",
                "tool_config": {"web-search": raw_config},
                "llm_provider_id": "23",
                "llm_provider_type": "anthropic",
                "llm_provider_model_id": "7",
            }
        )
    if not error_expected:
        assert isinstance(node.tool_config, dict)
        assert isinstance(node.tool_config["web-search"], AnthropicWebSearchToolConfig)
        assert node.tool_config["web-search"].model_dump() == {
            "allowed_domains": list(filter(None, allowed_domains)) or None,
            "blocked_domains": list(filter(None, blocked_domains)) or None,
        }
