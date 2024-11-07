import json
from functools import cached_property

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.custom_actions.utils import APIOperationDetails, get_operations_from_spec_dict, make_model_id
from apps.service_providers.auth_service import anonymous_auth_service
from apps.teams.models import BaseTeamModel


@audit_fields("team", "name", "prompt", "api_schema", audit_special_queryset_writes=True)
class CustomAction(BaseTeamModel):
    objects = AuditingManager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    prompt = models.TextField(blank=True)
    server_url = models.URLField()
    api_schema = models.JSONField()
    auth_provider = models.ForeignKey(
        "service_providers.AuthProvider",
        on_delete=models.SET_NULL,
        related_name="custom_actions",
        null=True,
        blank=True,
    )
    _operations = models.JSONField(default=list)
    allowed_operations = ArrayField(models.CharField(max_length=255), default=list)

    class Meta:
        ordering = ("name",)

    @property
    def operations(self) -> list[APIOperationDetails]:
        return [APIOperationDetails(**op) for op in self._operations]

    @operations.setter
    def operations(self, value: list[APIOperationDetails]):
        self._operations = [op.model_dump() for op in value]

    def save(self, *args, **kwargs):
        self.server_url = self.api_schema.get("servers", [{}])[0].get("url", "")
        try:
            self.operations = get_operations_from_spec_dict(self.api_schema)
        except Exception as e:
            raise ValidationError(f"Invalid OpenAPI schema: {e}")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @cached_property
    def api_schema_json(self):
        return json.dumps(self.api_schema, sort_keys=True)

    def get_absolute_url(self):
        return reverse("custom_actions:edit", args=[self.team.slug, self.pk])

    def get_auth_service(self):
        if self.auth_provider:
            return self.auth_provider.get_auth_service()
        return anonymous_auth_service

    def get_operations_by_id(self):
        return {op.operation_id: op for op in self.operations}


class CustomActionOperation(models.Model):
    experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        related_name="custom_action_operations",
        null=True,
        blank=True,
    )
    assistant = models.ForeignKey(
        "assistants.OpenAiAssistant",
        on_delete=models.CASCADE,
        related_name="custom_action_operations",
        null=True,
        blank=True,
    )
    custom_action = models.ForeignKey(CustomAction, on_delete=models.CASCADE)
    operation_id = models.CharField(max_length=255)

    class Meta:
        ordering = ("operation_id",)
        constraints = [
            models.CheckConstraint(
                check=Q(experiment__isnull=False) | Q(assistant__isnull=False),
                name="experiment_or_assistant_required",
            ),
            models.UniqueConstraint(
                fields=["custom_action", "operation_id"],
                condition=Q(experiment__isnull=False),
                name="unique_experiment_custom_action_operation",
            ),
            models.UniqueConstraint(
                fields=["custom_action", "operation_id"],
                condition=Q(assistant__isnull=False),
                name="unique_assistant_custom_action_operation",
            ),
        ]

    def __str__(self):
        return f"{self.custom_action}: {self.operation_id}"

    def get_model_id(self, with_holder=True):
        holder_id = self.experiment_id if self.experiment_id else self.assistant_id
        holder_id = holder_id if with_holder else ""
        return make_model_id(holder_id, self.custom_action_id, self.operation_id)
