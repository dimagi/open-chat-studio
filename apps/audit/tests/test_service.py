from apps.audit.service import AuditService

from .models import ModelWithSchemaField, TestSchema


def test_serialize_schema_field():
    model = ModelWithSchemaField(config=TestSchema(att1="value", att2=42, url_attr="http://example.com"))

    value = AuditService().get_field_value(model, "config")
    assert value == {"att1": "value", "att2": 42, "url_attr": "http://example.com/"}
