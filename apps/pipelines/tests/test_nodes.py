import pytest
from pydantic import BaseModel, TypeAdapter
from pydantic_core import ValidationError

from apps.pipelines.nodes.nodes import OptionalInt, SendEmail, StructuredDataSchemaValidatorMixin


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
