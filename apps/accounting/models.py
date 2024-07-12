from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.teams.models import BaseTeamModel


class UsageType(models.TextChoices):
    INPUT_TOKENS = "input_tokens", "Input Tokens"
    OUTPUT_TOKENS = "output_tokens", "Output Tokens"


class Usage(BaseTeamModel):
    # the source of the usage
    source_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="usage_by_source")
    source_object_id = models.PositiveBigIntegerField(null=True)
    source_object = GenericForeignKey("source_content_type", "source_object_id")

    # which service this usage is associated with
    service_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="usage_by_service")
    service_object_id = models.PositiveBigIntegerField(null=True)
    service_object = GenericForeignKey("service_content_type", "service_object_id")

    type = models.CharField(max_length=255)
    value = models.IntegerField(default=0)

    metadata = models.JSONField(default=dict)
