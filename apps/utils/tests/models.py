"""Models for testing deletion utilities and the version spec framework.

These only exist in the test database. Changes to the model classes require
rebuilding the test database (use `--create-db`).

Audited models: Collection, Bot, Tool
Unaudited models: Param, Webhook, CycleA, CycleB

Relationships:
- Bot -> Collection (CASCADE)
- Bot -> Tool (M2M)
- Tool -> Collection (SET_NULL)
- Param -> Tool (CASCADE)
- Webhook -> Bot (PROTECT)
- CycleA <-> CycleB (PROTECT both ways, for deletion-order cycle tests)

Versioned models (`BaseModel` + `VersionsMixin`, no spec of their own; tests bind throwaway specs):
- VersionedWidget -> VersionedPart twice (`part`, `live_part`), with a unique token to regenerate
"""

import uuid

from django.db import models
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.experiments.versioning import VersionsMixin
from apps.utils.models import BaseModel


@audit_fields("name", audit_special_queryset_writes=True)
class Bot(models.Model):
    name = models.CharField(max_length=100)
    tools = models.ManyToManyField("Tool")
    collection = models.ForeignKey("Collection", on_delete=models.CASCADE)

    objects = AuditingManager()

    def __str__(self):
        return self.name


@audit_fields("name", "collection", audit_special_queryset_writes=True)
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


class Webhook(models.Model):
    bot = models.ForeignKey(Bot, on_delete=models.PROTECT)

    def __str__(self):
        return str(self.pk)


class CycleA(models.Model):
    other = models.ForeignKey("CycleB", on_delete=models.PROTECT, null=True)

    def __str__(self):
        return str(self.pk)


class CycleB(models.Model):
    other = models.ForeignKey(CycleA, on_delete=models.PROTECT, null=True)

    def __str__(self):
        return str(self.pk)


MODEL_NAMES = [f"{model.__module__}.{model.__name__}" for model in [Bot, Tool, Collection, Param]]


class VersionedPart(BaseModel, VersionsMixin):
    name = models.CharField(max_length=100)
    working_version = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="versions"
    )
    is_archived = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class VersionedWidget(BaseModel, VersionsMixin):
    name = models.CharField(max_length=100)
    note = models.TextField(blank=True, default="")
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    part = models.ForeignKey(VersionedPart, on_delete=models.SET_NULL, null=True, blank=True, related_name="widgets")
    live_part = models.ForeignKey(
        VersionedPart, on_delete=models.SET_NULL, null=True, blank=True, related_name="live_widgets"
    )
    working_version = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="versions"
    )
    version_number = models.PositiveIntegerField(default=1)
    is_archived = models.BooleanField(default=False)

    def __str__(self):
        return self.name
