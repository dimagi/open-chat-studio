import logging

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.custom_actions.form_utils import make_model_id
from apps.custom_actions.schema_utils import (
    APIOperationDetails,
    get_operations_from_spec_dict,
    get_standalone_schema_for_action_operation,
)
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.service_providers.auth_service import anonymous_auth_service
from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team
from apps.utils.models import BaseModel

log = logging.getLogger("ocs.custom_actions")


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
    health_endpoint = models.URLField(blank=True, null=True, help_text="Optional health check endpoint")
    health_status = models.CharField(
        max_length=20,
        choices=[
            ("unknown", "Unknown"),
            ("up", "Up"),
            ("down", "Down"),
        ],
        default="unknown",
    )
    last_health_check = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("name",)

    @property
    def operations(self) -> list[APIOperationDetails]:
        return [APIOperationDetails(**op) for op in self._operations]

    @operations.setter
    def operations(self, value: list[APIOperationDetails]):
        self._operations = [op.model_dump() for op in value]

    def save(self, *args, **kwargs):
        try:
            self.operations = get_operations_from_spec_dict(self.api_schema)
        except Exception as e:
            raise ValidationError(f"Invalid OpenAPI schema: {e}") from e
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("custom_actions:edit", args=[get_slug_for_team(self.team_id), self.pk])

    def get_auth_service(self):
        if self.auth_provider:
            return self.auth_provider.get_auth_service()
        return anonymous_auth_service

    def get_operations_by_id(self):
        return {op.operation_id: op for op in self.operations}

    def detect_health_endpoint_from_spec(self) -> str | None:
        """
        Attempt to detect a health endpoint from the API schema.

        Searches for common health check endpoint patterns in the OpenAPI spec.
        Patterns are checked in priority order (most common first):
        - /health - Standard health check endpoint
        - /healthz - Kubernetes-style health check
        - /healthcheck - Alternative common pattern
        - /api/health - Namespaced health endpoint
        - /status - General status endpoint
        - /_health - Internal health check (often used in microservices)

        Returns:
            The full URL to the health endpoint if found, None otherwise.
        """
        if not self.api_schema or not self.server_url:
            return None

        health_paths = ["/health", "/healthz", "/healthcheck", "/api/health", "/status", "/_health"]

        paths = self.api_schema.get("paths", {})
        for health_path in health_paths:
            if health_path in paths:
                # Check if it has a GET method
                if "get" in paths[health_path]:
                    return f"{self.server_url.rstrip('/')}{health_path}"
        
        return None


class CustomActionOperationManager(VersionsObjectManagerMixin, models.Manager):
    pass


class CustomActionOperation(BaseModel, VersionsMixin):
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
    node = models.ForeignKey(
        "pipelines.Node",
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
                check=Q(experiment__isnull=False) | Q(assistant__isnull=False) | Q(node__isnull=False),
                name="experiment_or_assistant_or_node_required",
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
                log.warning("Versioned model is missing 'operation_schema'")
            return get_standalone_schema_for_action_operation(self)
        return self._operation_schema

    @operation_schema.setter
    def operation_schema(self, spec: dict):
        if not self.working_version_id:
            log.warning("Working Version should not have 'operation_schema' set")
            return
        self._operation_schema = spec

    def get_model_id(self, with_holder=True):
        holder_id = self.experiment_id if self.experiment_id else self.assistant_id
        holder_id = holder_id if with_holder else ""
        return make_model_id(holder_id, self.custom_action_id, self.operation_id)

    @transaction.atomic()
    def create_new_version(self, new_experiment=None, new_assistant=None, new_node=None, is_copy=False):
        action_holders = [new_experiment, new_assistant, new_node]
        if not action_holders:
            raise ValueError("Either new_experiment or new_assistant must be provided")
        if len([holder for holder in action_holders if holder is not None]) > 1:
            raise ValueError("Only one of new_experiment or new_assistant can be provided")
        new_instance = super().create_new_version(save=False, is_copy=is_copy)
        new_instance.experiment = new_experiment
        new_instance.assistant = new_assistant
        new_instance.node = new_node
        if not is_copy:
            new_instance.operation_schema = get_standalone_schema_for_action_operation(new_instance)
        new_instance.save()
        return new_instance

    def _get_version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="operation_schema", raw_value=self.operation_schema),
            ],
        )
