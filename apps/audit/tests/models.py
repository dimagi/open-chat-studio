"""Models for testing custom audit service.

These only exist in the test database. Changes to the model classes require
rebuilding the test database (use `--create-db`).
"""

from django.db import models
from django_pydantic_field import SchemaField
from pydantic import BaseModel, HttpUrl


class TestSchema(BaseModel):
    att1: str
    att2: int
    url_attr: HttpUrl


class ModelWithSchemaField(models.Model):
    config = SchemaField(TestSchema)

    def __str__(self):
        return f"{self.config}"
