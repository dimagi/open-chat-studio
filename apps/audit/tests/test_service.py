import json

from field_audit import enable_audit

from apps.audit.service import AuditService
from apps.audit.tests.models import ModelWithSchemaField, TestSchema


def test_serialize_schema_field():

    model = ModelWithSchemaField(config=TestSchema(att1="value", att2=42, url_attr="http://example.com"))

    value = AuditService().get_field_value(model, "config")
    assert value == {"att1": "value", "att2": 42, "url_attr": "http://example.com/"}


def test_make_audit_event_from_values_serializes_schema_field():
    """The audited delete path passes raw ``QuerySet.values()`` output (pydantic models for
    SchemaFields) here, so the resulting delta must be JSON serializable."""
    config = TestSchema(att1="value", att2=42, url_attr="http://example.com")
    with enable_audit():
        event = AuditService().make_audit_event_from_values(
            {"config": config}, {}, object_pk=1, object_cls=ModelWithSchemaField, request=None
        )

    assert event.delta == {"config": {"old": {"att1": "value", "att2": 42, "url_attr": "http://example.com/"}}}
    json.dumps(event.delta)  # must not raise
