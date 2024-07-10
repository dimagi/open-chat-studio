"""Models for testing deletion utilities.

These only exist in the test database. Changes to the model classes require
rebuilding the test database (use `--create-db`).

Audited models: Collection, Bot, Tool
Unaudited models: Param

Relationships:
- Bot -> Collection (CASCADE)
- Bot -> Tool (M2M)
- Tool -> Collection (SET_NULL)
- Param -> Tool (CASCADE)
"""
from django.db import models
from field_audit import audit_fields
from field_audit.models import AuditingManager


@audit_fields("name", audit_special_queryset_writes=True)
class Bot(models.Model):
    name = models.CharField(max_length=100)
    tools = models.ManyToManyField("Tool")
    collection = models.ForeignKey("Collection", on_delete=models.CASCADE)

    objects = AuditingManager()

    def __str__(self):
        return self.name


@audit_fields("name", audit_special_queryset_writes=True)
class Tool(models.Model):
    name = models.CharField(max_length=100)
    collection = models.ForeignKey("Collection", on_delete=models.SET_NULL, null=True, blank=True)

    objects = AuditingManager()

    def __str__(self):
        return self.name


class Param(models.Model):
    name = models.CharField(max_length=100)
    tool = models.ForeignKey(Tool, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


@audit_fields("name", audit_special_queryset_writes=True)
class Collection(models.Model):
    name = models.CharField(max_length=100)

    objects = AuditingManager()

    def __str__(self):
        return self.name
