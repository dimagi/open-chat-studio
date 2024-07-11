from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.teams.models import BaseTeamModel


class UsageType(models.TextChoices):
    INPUT_TOKENS = "input_tokens", "Input Tokens"
    OUTPUT_TOKENS = "output_tokens", "Output Tokens"


class Usage(BaseTeamModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True)
    object_id = models.PositiveBigIntegerField(null=True)
    content_object = GenericForeignKey("content_type", "object_id")
    type = models.CharField(max_length=255)
    value = models.IntegerField(default=0)
