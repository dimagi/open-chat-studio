from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditingManager
from langchain_community.tools import APIOperation
from langchain_community.utilities.openapi import OpenAPISpec
from pydantic import BaseModel

from apps.custom_actions.form_utils import make_model_id
from apps.custom_actions.schema_utils import get_standalone_schema_for_action_operation
from apps.experiments.models import VersionsMixin, VersionsObjectManagerMixin
from apps.service_providers.auth_service import anonymous_auth_service
from apps.teams.models import BaseTeamModel
from apps.utils.models import VersioningMixin


class APIOperationDetails(BaseModel):
    operation_id: str
    description: str
    path: str
    method: str

    def __str__(self):
        return f"{self.method.upper()}: {self.description}"


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
            self.operations = self._get_operations_from_spec()
        except Exception as e:
            raise ValidationError(f"Invalid OpenAPI schema: {e}")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("custom_actions:edit", args=[self.team.slug, self.pk])

    def get_auth_service(self):
        if self.auth_provider:
            return self.auth_provider.get_auth_service()
        return anonymous_auth_service

    def get_operations_by_id(self):
        return {op.operation_id: op for op in self.operations}

    def _get_operations_from_spec(self):
        operations = []
        spec = OpenAPISpec.from_spec_dict(self.api_schema)
        for path in spec.paths:
            for method in spec.get_methods_for_path(path):
                op = APIOperation.from_openapi_spec(spec, path, method)
                operations.append(
                    APIOperationDetails(
                        operation_id=op.operation_id, description=op.description, path=path, method=method
                    )
                )
        return operations


class CustomActionOperationManager(VersionsObjectManagerMixin, models.Manager):
    pass


class CustomActionOperation(models.Model, VersionsMixin, VersioningMixin):
    working_version = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="versions",
    )
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
    _operation_schema = models.JSONField(default=dict)

    objects = CustomActionOperationManager()

    class Meta:
        ordering = ("operation_id",)
        constraints = [
            models.CheckConstraint(
                check=Q(experiment__isnull=False) | Q(assistant__isnull=False),
                name="experiment_or_assistant_required",
            ),
            models.UniqueConstraint(
                fields=["experiment", "custom_action", "operation_id"],
                condition=Q(experiment__isnull=False),
                name="unique_experiment_custom_action_operation",
            ),
            models.UniqueConstraint(
                fields=["assistant", "custom_action", "operation_id"],
                condition=Q(assistant__isnull=False),
                name="unique_assistant_custom_action_operation",
            ),
        ]

    def __str__(self):
        return f"{self.custom_action}: {self.operation_id}"

    @property
    def operation_schema(self) -> dict:
        if not self._operation_schema:
            if self.working_version_id:
                raise ValueError("Missing OpenAPI spec for versioned operation")
            return get_standalone_schema_for_action_operation(self)
        return self._operation_schema

    @operation_schema.setter
    def operation_schema(self, spec: dict):
        if not self.working_version_id:
            raise ValueError("Working Version should not have 'operation_schema' set")
        self._operation_schema = spec

    def get_model_id(self, with_holder=True):
        holder_id = self.experiment_id if self.experiment_id else self.assistant_id
        holder_id = holder_id if with_holder else ""
        return make_model_id(holder_id, self.custom_action_id, self.operation_id)

    @transaction.atomic()
    def create_new_version(self, new_experiment=None, new_assistant=None):
        if not (new_experiment or new_assistant):
            raise ValueError("Either new_experiment or new_assistant must be provided")
        if new_experiment and new_assistant:
            raise ValueError("Only one of new_experiment or new_assistant can be provided")
        new_instance = super().create_new_version(save=False)
        new_instance.experiment = new_experiment
        new_instance.assistant = new_assistant
        new_instance.operation_schema = get_standalone_schema_for_action_operation(new_instance)
        new_instance.save()
        return new_instance

    def get_fields_to_exclude(self):
        return super().get_fields_to_exclude() + ["experiment", "assistant"]
