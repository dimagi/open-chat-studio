import base64
import secrets
import uuid
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.urls import reverse
from django_cryptography.fields import encrypt

from apps.generics.chips import Chip
from apps.teams.models import BaseTeamModel, Team
from apps.teams.utils import get_slug_for_team
from apps.utils.fields import SanitizedJSONField


class Participant(BaseTeamModel):
    name = models.CharField(max_length=320, blank=True)
    identifier = models.CharField(max_length=320, blank=True)  # max email length
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    platform = models.CharField(max_length=32)
    remote_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "experiments_participant"
        ordering = ["platform", "identifier"]
        unique_together = [("team", "platform", "identifier")]

    @classmethod
    def create_anonymous(cls, team: Team, platform: str, remote_id: str = "") -> "Participant":
        public_id = str(uuid.uuid4())
        return cls.objects.create(
            team=team,
            platform=platform,
            identifier=f"anon:{public_id}",
            public_id=public_id,
            name="Anonymous",
            remote_id=remote_id,
        )

    @property
    def is_anonymous(self):
        return self.identifier == f"anon:{self.public_id}"

    @property
    def email(self):
        validate_email(self.identifier)
        return self.identifier

    @property
    def global_data(self):
        if self.name:
            return {"name": self.name}
        return {}

    def update_name_from_data(self, data: dict):
        """
        Updates participant name field from a data dictionary.
        """
        if "name" in data:
            self.name = data["name"]
            self.save(update_fields=["name"])

    def __str__(self):
        if self.is_anonymous:
            suffix = str(self.public_id)[:6]
            return f"Anonymous [{suffix}]"
        if self.name:
            return f"{self.name} ({self.identifier})"
        if self.user and self.user.get_full_name():
            return f"{self.user.get_full_name()} ({self.identifier})"
        return self.identifier

    def get_platform_display(self):
        from apps.channels.models import ChannelPlatform

        try:
            return ChannelPlatform(self.platform).label
        except ValueError:
            return self.platform

    def get_latest_session(self, experiment):
        return self.experimentsession_set.filter(experiment=experiment).order_by("-created_at").first()

    def last_seen(self) -> datetime:
        """Gets the "last seen" date for this participant based on their last message"""
        from apps.chat.models import ChatMessage

        latest_session = (
            self.experimentsession_set.annotate(message_count=Count("chat__messages"))
            .exclude(message_count=0)
            .order_by("-created_at")
            .values("id")[:1]
        )
        return (
            ChatMessage.objects.filter(chat__experiment_session=models.Subquery(latest_session), message_type="human")
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )

    def get_absolute_url(self):
        experiment = self.get_experiments_for_display().first()
        if experiment:
            return self.get_link_to_experiment_data(experiment)
        return reverse("participants:single-participant-home", args=[get_slug_for_team(self.team_id), self.id])

    def get_link_to_experiment_data(self, experiment) -> str:
        url = reverse(
            "participants:single-participant-home-with-experiment",
            args=[get_slug_for_team(self.team_id), self.id, experiment.id],
        )
        return f"{url}#{experiment.id}"

    def get_experiments_for_display(self):
        """Used by the html templates to display various stats about the participant's participation."""
        from apps.chat.models import ChatMessage
        from apps.experiments.models import Experiment

        exp_scoped_human_message = ChatMessage.objects.filter(
            chat__experiment_session__participant=self,
            message_type="human",
            chat__experiment_session__experiment__id=OuterRef("id"),
        )
        last_message = exp_scoped_human_message.order_by("-created_at")[:1].values("created_at")
        joined_on = self.experimentsession_set.order_by("created_at")[:1].values("created_at")
        return (
            Experiment.objects.get_all()
            .annotate(
                joined_on=Subquery(joined_on),
                last_message=Subquery(last_message),
            )
            .filter(Q(sessions__participant=self) | Q(id__in=Subquery(self.data_set.values("experiment"))))
            .distinct()
        )

    def get_data_for_experiment(self, experiment) -> dict:
        try:
            return self.data_set.get(experiment=experiment).data or {}
        except ParticipantData.DoesNotExist:
            return {}

    def get_schedules_for_experiment(
        self, experiment, as_dict=False, as_timezone: str | None = None, include_inactive=False
    ):
        """
        Returns all scheduled messages for the associated participant for this session's experiment as well as
        any child experiments in the case where the experiment is a parent

        Parameters:
        as_dict: If True, the data will be returned as an array of dictionaries, otherwise an an array of strings
        timezone: The timezone to use for the dates. Defaults to the active timezone.
        """
        from apps.events.models import ScheduledMessage
        from apps.experiments.models import ExperimentRoute

        child_experiments = ExperimentRoute.objects.filter(team=self.team, parent=experiment).values("child")
        messages = (
            ScheduledMessage.objects.filter(
                Q(experiment=experiment) | Q(experiment__in=models.Subquery(child_experiments)),
                participant=self,
                team=self.team,
            )
            .select_related("action")
            .prefetch_related("attempts")
            .order_by("created_at")
        )
        if not include_inactive:
            messages = messages.filter(is_complete=False, cancelled_at=None)

        scheduled_messages = []
        for message in messages:
            if as_dict:
                scheduled_messages.append(message.as_dict(as_timezone=as_timezone))
            else:
                scheduled_messages.append(message.as_string(as_timezone=as_timezone))
        return scheduled_messages

    @transaction.atomic()
    def update_memory(self, data: dict, experiment):
        """
        Updates this participant's data records by merging `data` with the existing data. By default, data for all
        experiments that this participant participated in will be updated.

        Paramters
        data:
            A dictionary containing the new data
        experiment:
            Create a new record for this experiment if one does not exist
        """
        # Update all existing records
        participant_data = ParticipantData.objects.filter(participant=self).select_for_update()
        experiments = set()
        with transaction.atomic():
            for record in participant_data:
                experiments.add(record.experiment_id)
                record.data = record.data | data
            ParticipantData.objects.bulk_update(participant_data, fields=["data"])

        if experiment.id not in experiments:
            ParticipantData.objects.create(team=self.team, experiment=experiment, data=data, participant=self)

    def as_chip(self) -> Chip:
        return Chip(label=self.identifier, url=self.get_absolute_url())


class ParticipantDataObjectManager(models.Manager):
    def for_experiment(self, experiment):
        experiment_id = experiment.id
        if experiment.is_a_version:
            experiment_id = experiment.working_version_id
        return super().get_queryset().filter(experiment_id=experiment_id, team=experiment.team)


def validate_json_dict(value):
    """Participant data must be a dict"""
    if not isinstance(value, dict):
        raise ValidationError("JSON object must be a dictionary")


class ParticipantData(BaseTeamModel):
    objects = ParticipantDataObjectManager()
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="data_set")
    data = encrypt(SanitizedJSONField(default=dict, validators=[validate_json_dict]))
    experiment = models.ForeignKey("experiments.Experiment", on_delete=models.CASCADE)
    system_metadata = SanitizedJSONField(default=dict)
    encryption_key = encrypt(
        models.CharField(max_length=255, blank=True, help_text="The base64 encoded encryption key")
    )

    def get_encryption_key_bytes(self):
        return base64.b64decode(self.encryption_key)

    def generate_encryption_key(self):
        key = base64.b64encode(secrets.token_bytes(32)).decode("utf-8")
        self.encryption_key = key
        self.save(update_fields=["encryption_key"])

    def has_consented(self) -> bool:
        return self.system_metadata.get("consent", False)

    def update_consent(self, consent: bool):
        self.system_metadata["consent"] = consent
        self.save(update_fields=["system_metadata"])

    class Meta:
        db_table = "experiments_participantdata"
        indexes = [
            models.Index(fields=["experiment"]),
        ]
        # A bot cannot have a link to multiple data entries for the same Participant
        # Multiple bots can have a link to the same ParticipantData record
        # A participant can have many participant data records
        unique_together = ("participant", "experiment")
