import json
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import validate_email
from django.db import models
from django.db.models import Count, OuterRef, Subquery
from django.urls import reverse
from django_cryptography.fields import encrypt

from apps.chat.models import ChatMessage
from apps.teams.models import BaseTeamModel

if TYPE_CHECKING:
    from apps.experiments.models import Experiment, ExperimentSession

log = logging.getLogger(__name__)


class Participant(BaseTeamModel):
    identifier = models.CharField(max_length=320, blank=True)  # max email length
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)

    @property
    def email(self):
        validate_email(self.identifier)
        return self.identifier

    def __str__(self):
        return self.identifier

    def get_latest_session(self, experiment: "Experiment") -> "ExperimentSession":
        return self.experimentsession_set.filter(experiment=experiment).order_by("-created_at").first()

    def last_seen(self) -> datetime:
        """Gets the "last seen" date for this participant based on their last message"""
        latest_session = (
            self.experimentsession_set.annotate(chat_count=Count("chat__messages"))
            .exclude(chat_count=0)
            .order_by("-created_at")
            .values("id")[:1]
        )

        message = (
            ChatMessage.objects.filter(chat__experiment_session=models.Subquery(latest_session), message_type="human")
            .order_by("-created_at")
            .first()
        )
        return message.created_at if message else None

    def get_absolute_url(self):
        return reverse("participants:single-participant-home", args=[self.team.slug, self.id])

    def get_experiments_for_display(self):
        """Used by the html templates to display various stats about the participant's participation."""
        from apps.experiments.models import Experiment

        exp_scoped_human_message = ChatMessage.objects.filter(
            chat__experiment_session__participant=self,
            message_type="human",
            chat__experiment_session__experiment__id=OuterRef("id"),
        )
        joined_on = exp_scoped_human_message.order_by("created_at")[:1].values("created_at")
        last_message = exp_scoped_human_message.order_by("-created_at")[:1].values("created_at")
        return (
            Experiment.objects.annotate(
                joined_on=Subquery(joined_on),
                last_message=Subquery(last_message),
            )
            .filter(sessions__participant=self)
            .distinct()
        )

    class Meta:
        ordering = ["identifier"]
        unique_together = [("team", "identifier")]


class ParticipantData(BaseTeamModel):
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="data_set")
    data = encrypt(models.JSONField(default=dict))
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]
        # A bot cannot have a link to multiple data entries for the same Participant
        # Multiple bots can have a link to the same ParticipantData record
        # A participant can have many participant data records
        unique_together = ("participant", "content_type", "object_id")

    @property
    def as_json(self):
        return json.dumps(self.data, indent=2)
