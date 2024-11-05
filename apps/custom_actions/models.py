import json
from functools import cached_property

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditingManager
from langchain_community.tools import APIOperation
from langchain_community.utilities.openapi import OpenAPISpec

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
    operation_ids = ArrayField(models.CharField(max_length=255), default=list, blank=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        self.server_url = self.api_schema.get("servers", [{}])[0].get("url", "")
        self.operation_ids = list(self.get_operations_mapping())
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

    def get_operations_mapping(self):
        operations_by_id = {}
        spec = OpenAPISpec.from_spec_dict(self.api_schema)
        for path in spec.paths:
            for method in spec.get_methods_for_path(path):
                op = APIOperation.from_openapi_spec(spec, path, method)
                operations_by_id[op.operation_id] = op
        return operations_by_id


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
    custom_action = models.ForeignKey(CustomAction, on_delete=models.CASCADE, related_name="operations")
    operation_id = models.CharField(max_length=255)

    class Meta:
        ordering = ("operation_id",)
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "custom_action", "operation_id"],
                name="unique_experiment_custom_action_operation",
            ),
            models.UniqueConstraint(
                fields=["assistant", "custom_action", "operation_id"],
                name="unique_assistant_custom_action_operation",
            ),
        ]

    def __str__(self):
        return f"{self.custom_action}: {self.operation_id}"

    def get_model_id(self, with_holder=True):
        holder_id = self.experiment_id if self.experiment_id else self.assistant_id
        holder_id = holder_id if with_holder else ""
        return make_model_id(holder_id, self.custom_action_id, self.operation_id)


def make_model_id(holder_id, custom_action_id, operation_id):
    ret = f"{custom_action_id}:{operation_id}"
    if holder_id:
        ret = f"{holder_id}:{ret}"
    return ret
