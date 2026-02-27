from apps.audit.service import AuditService


def test_serialize_schema_field():
    from .models import ModelWithSchemaField, TestSchema

    model = ModelWithSchemaField(config=TestSchema(att1="value", att2=42, url_attr="http://example.com"))  # ty: ignore[invalid-argument-type]

    value = AuditService().get_field_value(model, "config")
    assert value == {"att1": "value", "att2": 42, "url_attr": "http://example.com/"}
