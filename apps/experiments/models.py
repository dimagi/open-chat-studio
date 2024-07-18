import json
import logging
import uuid
from datetime import datetime
from functools import cached_property

import markdown
import pytz
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db import models, transaction
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django_cryptography.fields import encrypt
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments import model_audit_fields
from apps.teams.models import BaseTeamModel, Team
from apps.utils.models import BaseModel
from apps.web.meta import absolute_url

log = logging.getLogger(__name__)


class PromptObjectManager(AuditingManager):
    pass


class ExperimentObjectManager(AuditingManager):
    pass


class SourceMaterialObjectManager(AuditingManager):
    pass


class SafetyLayerObjectManager(AuditingManager):
    pass


class ConsentFormObjectManager(AuditingManager):
    pass


class SyntheticVoiceObjectManager(AuditingManager):
    pass


class PromptBuilderHistory(BaseTeamModel):
    """
    History entries for the prompt builder
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    history = models.JSONField()

    def __str__(self) -> str:
        return str(self.history)


@audit_fields(*model_audit_fields.SOURCE_MATERIAL_FIELDS, audit_special_queryset_writes=True)
class SourceMaterial(BaseTeamModel):
    """
    Some Source Material on a particular topic.
    """

    objects = SourceMaterialObjectManager()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    topic = models.CharField(max_length=50)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the source material.")  # noqa DJ001
    material = models.TextField()

    class Meta:
        ordering = ["topic"]

    def __str__(self):
        return self.topic

    def get_absolute_url(self):
        return reverse("experiments:source_material_edit", args=[self.team.slug, self.id])


@audit_fields(*model_audit_fields.SAFETY_LAYER_FIELDS, audit_special_queryset_writes=True)
class SafetyLayer(BaseTeamModel):
    objects = SafetyLayerObjectManager()
    name = models.CharField(max_length=128)
    prompt_text = models.TextField()
    messages_to_review = models.CharField(
        choices=ChatMessageType.safety_layer_choices,
        default=ChatMessageType.HUMAN,
        help_text="Whether the prompt should be applied to human or AI messages",
        max_length=10,
    )
    default_response_to_user = models.TextField(
        blank=True,
        default="",
        help_text="If specified, the message that will be sent to the user instead of the filtered message.",
    )
    prompt_to_bot = models.TextField(
        blank=True,
        default="",
        help_text="If specified, the message that will be sent to the bot instead of the filtered message.",
    )

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("experiments:safety_edit", args=[self.team.slug, self.id])


class Survey(BaseTeamModel):
    """
    A survey.
    """

    name = models.CharField(max_length=128)
    url = models.URLField(max_length=500)
    confirmation_text = models.TextField(
        null=False,
        default=(
            "Please complete the following survey by clicking on the survey link."
            " When you have finished, respond with '1' to let us know that you've completed it."
            " Survey link: {survey_link}"
        ),
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_link(self, participant, experiment_session):
        participant_public_id = participant.public_id if participant else "[anonymous]"
        return self.url.format(
            participant_id=participant_public_id,
            session_id=experiment_session.external_id,
            experiment_id=experiment_session.experiment.public_id,
        )

    def get_absolute_url(self):
        return reverse("experiments:survey_edit", args=[self.team.slug, self.id])


@audit_fields(*model_audit_fields.CONSENT_FORM_FIELDS, audit_special_queryset_writes=True)
class ConsentForm(BaseTeamModel):
    """
    Custom markdown consent form to be used by experiments.
    """

    objects = ConsentFormObjectManager()
    name = models.CharField(max_length=128)
    consent_text = models.TextField(help_text="Custom markdown text")
    capture_identifier = models.BooleanField(default=True)
    identifier_label = models.CharField(max_length=200, default="Email Address")
    identifier_type = models.CharField(choices=(("email", "Email"), ("text", "Text")), default="email", max_length=16)
    is_default = models.BooleanField(default=False, editable=False)
    confirmation_text = models.CharField(
        null=False,
        default="Respond with '1' if you agree",
        help_text=("Use this text to tell the user to respond with '1' in order to give their consent"),
    )

    class Meta:
        ordering = ["name"]

    @classmethod
    def get_default(cls, team):
        return cls.objects.get(team=team, is_default=True)

    def __str__(self):
        return self.name

    def get_rendered_content(self):
        return markdown.markdown(self.consent_text)

    def get_absolute_url(self):
        return reverse("experiments:consent_edit", args=[self.team.slug, self.id])


@audit_fields(*model_audit_fields.SYNTHETIC_VOICE_FIELDS, audit_special_queryset_writes=True)
class SyntheticVoice(BaseModel):
    """
    A synthetic voice as per the service documentation. This is used when synthesizing responses for an experiment

    See AWS' docs for all available voices
    https://docs.aws.amazon.com/polly/latest/dg/voicelist.html
    """

    GENDERS = (
        ("male", "Male"),
        ("female", "Female"),
        ("male (child)", "Male (Child)"),
        ("female (child)", "Female (Child)"),
    )

    AWS = "AWS"
    Azure = "Azure"
    OpenAI = "OpenAI"
    OpenAIVoiceEngine = "OpenAIVoiceEngine"

    SERVICES = (
        ("AWS", AWS),
        ("Azure", Azure),
        ("OpenAI", OpenAI),
        ("OpenAIVoiceEngine", OpenAIVoiceEngine),
    )
    TEAM_SCOPED_SERVICES = [OpenAIVoiceEngine]

    objects = SyntheticVoiceObjectManager()
    name = models.CharField(
        max_length=128, help_text="The name of the synthetic voice, as per the documentation of the service"
    )
    neural = models.BooleanField(default=False, help_text="Indicates whether this voice is a neural voice")
    language = models.CharField(null=False, blank=False, max_length=64, help_text="The language this voice is for")
    language_code = models.CharField(
        null=False, blank=False, max_length=32, help_text="The language code this voice is for"
    )

    gender = models.CharField(
        null=False, blank=True, choices=GENDERS, max_length=14, help_text="The gender of this voice"
    )
    service = models.CharField(
        null=False, blank=False, choices=SERVICES, max_length=17, help_text="The service this voice is from"
    )
    voice_provider = models.ForeignKey(
        "service_providers.VoiceProvider", verbose_name=gettext("Team"), on_delete=models.CASCADE, null=True
    )
    file = models.ForeignKey("files.File", null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["name"]
        unique_together = ("name", "language_code", "language", "gender", "neural", "service", "voice_provider")

    def get_gender(self):
        # This is a bit of a hack to display the gender on the admin screen. Directly calling gender doesn't work
        return self.gender

    def __str__(self):
        prefix = "*" if self.neural else ""
        display_str = f"{prefix}{self.name}"
        if self.gender:
            display_str = f"{self.gender}: {display_str}"
        if self.language:
            display_str = f"{self.language}, {display_str}"
        return display_str

    @staticmethod
    def get_for_team(team: Team, exclude_services=None) -> list["SyntheticVoice"]:
        """Returns a queryset for this team comprising of all general synthetic voice records and those exclusive
        to this team. Any services specified by `exclude_services` will be excluded from the final result"""
        exclude_services = exclude_services or []
        general_services = ~Q(service__in=SyntheticVoice.TEAM_SCOPED_SERVICES) & Q(voice_provider__isnull=True)
        team_services = Q(voice_provider__team=team)
        return SyntheticVoice.objects.filter(general_services | team_services, ~Q(service__in=exclude_services))


class VoiceResponseBehaviours(models.TextChoices):
    ALWAYS = "always", gettext("Always")
    RECIPROCAL = "reciprocal", gettext("Reciprocal")
    NEVER = "never", gettext("Never")


class AgentTools(models.TextChoices):
    RECURRING_REMINDER = "recurring-reminder", gettext("Recurring Reminder")
    ONE_OFF_REMINDER = "one-off-reminder", gettext("One-off Reminder")
    SCHEDULE_UPDATE = "schedule-update", gettext("Schedule Update")


@audit_fields(*model_audit_fields.EXPERIMENT_FIELDS, audit_special_queryset_writes=True)
class Experiment(BaseTeamModel):
    """
    An experiment combines a chatbot prompt, a safety prompt, and source material.
    Each experiment can be run as a chatbot.
    """

    objects = ExperimentObjectManager()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=128)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the experiment.")  # noqa DJ001
    llm_provider = models.ForeignKey(
        "service_providers.LlmProvider", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="LLM Provider"
    )
    llm = models.CharField(max_length=255, help_text="The LLM model to use.", verbose_name="LLM Model", blank=True)
    assistant = models.ForeignKey(
        "assistants.OpenAiAssistant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="OpenAI Assistant",
    )
    temperature = models.FloatField(default=0.7, validators=[MinValueValidator(0), MaxValueValidator(1)])

    prompt_text = models.TextField(blank=True, default="")
    input_formatter = models.TextField(
        blank=True,
        default="",
        help_text="Use the {input} variable somewhere to modify the user input before it reaches the bot. "
        "E.g. 'Safe or unsafe? {input}'",
    )
    safety_layers = models.ManyToManyField(SafetyLayer, related_name="experiments", blank=True)
    is_active = models.BooleanField(
        default=True, help_text="If unchecked, this experiment will be hidden from everyone besides the owner."
    )

    source_material = models.ForeignKey(
        SourceMaterial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="If provided, the source material will be given to every bot in the chain.",
    )
    seed_message = models.TextField(
        blank=True,
        default="",
        help_text="If set, send this message to the bot when the session starts, "
        "and prompt the user with the initial response.",
    )
    pre_survey = models.ForeignKey(
        Survey, null=True, blank=True, related_name="experiments_pre", on_delete=models.SET_NULL
    )
    post_survey = models.ForeignKey(
        Survey, null=True, blank=True, related_name="experiments_post", on_delete=models.SET_NULL
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    consent_form = models.ForeignKey(
        ConsentForm,
        on_delete=models.CASCADE,
        related_name="experiments",
        help_text="Consent form content to show to users before participation in experiments.",
    )
    voice_provider = models.ForeignKey(
        "service_providers.VoiceProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Voice Provider",
    )
    synthetic_voice = models.ForeignKey(
        SyntheticVoice, null=True, blank=True, related_name="experiments", on_delete=models.SET_NULL
    )
    conversational_consent_enabled = models.BooleanField(
        default=False,
        help_text=(
            "If enabled, the consent form will be sent at the start of a conversation for external channels. Note: "
            "This requires the experiment to have a seed message."
        ),
    )
    safety_violation_notification_emails = ArrayField(
        models.CharField(max_length=512),
        default=list,
        verbose_name="Safety violation notification emails",
        help_text="Email addresses to notify when the safety bot detects a violation. Separate addresses with a comma.",
        null=True,
        blank=True,
    )
    max_token_limit = models.PositiveIntegerField(
        default=8192,
        help_text="When the message history for a session exceeds this limit (in tokens), it will be compressed. "
        "If 0, compression will be disabled which may result in errors or high LLM costs.",
    )
    voice_response_behaviour = models.CharField(
        max_length=10,
        choices=VoiceResponseBehaviours.choices,
        default=VoiceResponseBehaviours.RECIPROCAL,
        help_text="This tells the bot when to reply with voice messages",
    )
    files = models.ManyToManyField("files.File", blank=True)
    participant_data = GenericRelation("experiments.ParticipantData", related_query_name="bots")
    children = models.ManyToManyField(
        "Experiment", blank=True, through="ExperimentRoute", symmetrical=False, related_name="parents"
    )
    tools = ArrayField(models.CharField(max_length=128), default=list, blank=True)

    class Meta:
        ordering = ["name"]
        permissions = [
            ("invite_participants", "Invite experiment participants"),
            ("download_chats", "Download experiment chats"),
        ]

    def __str__(self):
        return self.name

    @property
    def tools_enabled(self):
        return len(self.tools) > 0

    @property
    def event_triggers(self):
        return [*self.timeout_triggers.all(), *self.static_triggers.all()]

    def get_chat_model(self):
        service = self.get_llm_service()
        return service.get_chat_model(self.llm, self.temperature)

    def get_llm_service(self):
        if self.llm_provider:
            return self.llm_provider.get_llm_service()
        elif self.assistant:
            return self.assistant.llm_provider.get_llm_service()

    def get_absolute_url(self):
        return reverse("experiments:single_experiment_home", args=[self.team.slug, self.id])


class ExperimentRoute(BaseTeamModel):
    """
    Through model for Experiment.children routes.
    """

    parent = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="child_links")
    child = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="parent_links")
    keyword = models.SlugField(max_length=128)
    is_default = models.BooleanField(default=False)

    @classmethod
    def eligible_children(cls, team: Team, parent: Experiment | None = None):
        """Returns a list of experiments: that are not parents, and are not children of the current experiment"""
        parent_ids = cls.objects.filter(team=team).values_list("parent_id", flat=True).distinct()

        if parent:
            child_ids = cls.objects.filter(parent=parent).values_list("child_id", flat=True)
            eligible_experiments = (
                Experiment.objects.filter(team=team)
                .exclude(id__in=child_ids)
                .exclude(id__in=parent_ids)
                .exclude(id=parent.id)
            )
        else:
            eligible_experiments = Experiment.objects.filter(team=team).exclude(id__in=parent_ids)

        return eligible_experiments

    class Meta:
        unique_together = (
            ("parent", "child"),
            ("parent", "keyword"),
        )


class Participant(BaseTeamModel):
    identifier = models.CharField(max_length=320, blank=True)  # max email length
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    platform = models.CharField(max_length=32)

    class Meta:
        ordering = ["platform", "identifier"]
        unique_together = [("team", "platform", "identifier")]

    @property
    def email(self):
        validate_email(self.identifier)
        return self.identifier

    def __str__(self):
        return self.identifier

    def get_platform_display(self):
        from apps.channels.models import ChannelPlatform

        try:
            return ChannelPlatform(self.platform).label
        except ValueError:
            return self.platform

    def get_latest_session(self, experiment: Experiment) -> "ExperimentSession":
        return self.experimentsession_set.filter(experiment=experiment).order_by("-created_at").first()

    def last_seen(self) -> datetime:
        """Gets the "last seen" date for this participant based on their last message"""
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
        return reverse("participants:single-participant-home", args=[self.team.slug, self.id])

    def get_experiments_for_display(self):
        """Used by the html templates to display various stats about the participant's participation."""
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

    @transaction.atomic()
    def update_memory(self, data: dict, experiment: Experiment | None = None):
        """
        Updates this participant's data records by merging `data` with the existing data. By default, data for all
        experiments the this participant participated in will be updated. If there are no records for a specific
        experiment, one will be created.

        Paramters
        data:
            A dictionary containing the new data
        experiment:
            If specified, only the data for this experiment will be updated
        """
        experiments = Experiment.objects.filter(team=self.team).prefetch_related(
            Prefetch("participant_data", queryset=ParticipantData.objects.filter(participant=self))
        )
        if experiment:
            experiments = experiments.filter(id=experiment.id)

        records_to_update = []
        for experiment in experiments:
            participant_data = experiment.participant_data.first()
            # We cannot update the participant data using a single query, since the `data` field is encrypted at
            # the application level
            if participant_data:
                participant_data.data = participant_data.data | data
                records_to_update.append(participant_data)
            else:
                ParticipantData.objects.create(team=self.team, content_object=experiment, data=data, participant=self)

        ParticipantData.objects.bulk_update(records_to_update, fields=["data"])


class ParticipantDataObjectManager(models.Manager):
    def for_experiment(self, experiment: Experiment):
        return (
            super()
            .get_queryset()
            .filter(content_type__model="experiment", object_id=experiment.id, team=experiment.team)
        )


class ParticipantData(BaseTeamModel):
    objects = ParticipantDataObjectManager()
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


class SessionStatus(models.TextChoices):
    SETUP = "setup", gettext("Setting Up")
    PENDING = "pending", gettext("Awaiting participant")
    PENDING_PRE_SURVEY = "pending-pre-survey", gettext("Awaiting pre-survey")
    ACTIVE = "active", gettext("Active")
    PENDING_REVIEW = "pending-review", gettext("Awaiting final review.")
    COMPLETE = "complete", gettext("Complete")
    # CANCELLED = "cancelled", gettext("Cancelled")  # not used anywhere yet
    UNKNOWN = "unknown", gettext("Unknown")


class ExperimentSessionObjectManager(models.Manager):
    def for_chat_id(self, chat_id: str) -> list["ExperimentSession"]:
        return self.filter(participant__identifier=chat_id)

    def with_last_message_created_at(self):
        last_message_created_at = (
            ChatMessage.objects.filter(
                chat__experiment_session=models.OuterRef("pk"),
            )
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        return self.annotate(last_message_created_at=models.Subquery(last_message_created_at))


class ExperimentSession(BaseTeamModel):
    """
    An individual session, e.g. an instance of a chat with an experiment
    """

    objects = ExperimentSessionObjectManager()
    external_id = models.CharField(max_length=255, default=uuid.uuid4, unique=True)
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=20, choices=SessionStatus.choices, default=SessionStatus.SETUP)
    consent_date = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, help_text="When the experiment (chat) ended.")
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="When the final review was submitted.")

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="sessions")
    chat = models.OneToOneField(Chat, related_name="experiment_session", on_delete=models.CASCADE)
    seed_task_id = models.CharField(
        max_length=40, blank=True, default="", help_text="System ID of the seed message task, if present."
    )
    experiment_channel = models.ForeignKey(
        "channels.ExperimentChannel",
        on_delete=models.SET_NULL,
        related_name="experiment_sessions",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not hasattr(self, "chat"):
            self.chat = Chat.objects.create(team=self.team, name=self.experiment.name)
        if not self.external_id:
            self.external_id = str(uuid.uuid4())

        super().save(*args, **kwargs)

    def has_display_messages(self) -> bool:
        return bool(self.get_messages_for_display())

    def get_messages_for_display(self):
        if self.seed_task_id:
            return self.chat.messages.all()[1:]
        else:
            return self.chat.messages.all()

    def get_participant_display(self) -> str:
        if self.participant:
            return str(self.participant)
        elif self.user:
            return str(self.user)
        else:
            return "Anonymous"

    def get_invite_url(self) -> str:
        return absolute_url(
            reverse(
                "experiments:start_session_from_invite",
                args=[self.team.slug, self.experiment.public_id, self.external_id],
            )
        )

    def user_already_engaged(self) -> bool:
        return ChatMessage.objects.filter(chat=self.chat, message_type=ChatMessageType.HUMAN).exists()

    def get_platform_name(self) -> str:
        return self.experiment_channel.get_platform_display()

    def get_pre_survey_link(self):
        return self.experiment.pre_survey.get_link(self.participant, self)

    def get_post_survey_link(self):
        return self.experiment.post_survey.get_link(self.participant, self)

    def is_stale(self) -> bool:
        """A Channel Session is considered stale if the experiment that the channel points to differs from the
        one that the experiment session points to. This will happen when the user repurposes the channel to point
        to another experiment."""
        return self.experiment_channel.experiment != self.experiment

    def is_complete(self):
        return self.status == SessionStatus.COMPLETE

    def update_status(self, new_status: SessionStatus, commit: bool = True):
        self.status = new_status
        if commit:
            self.save()

    def get_absolute_edit_url(self):
        return reverse(
            "experiments:experiment_session_view", args=[self.team.slug, self.experiment.public_id, self.external_id]
        )

    def end(self, commit: bool = True, propagate: bool = True):
        """
        Ends this experiment session

        Args:
            commit: Whether to save the model after setting the ended_at value
            propagate: Whether to enqueue any static event triggers defined for this experiment_session
        Raises:
            ValueError: If propagate is True but commit is not.
        """
        self.update_status(SessionStatus.PENDING_REVIEW)
        if propagate and not commit:
            raise ValueError("Commit must be True when propagate is True")
        self.ended_at = timezone.now()
        if commit:
            self.save()
        if commit and propagate:
            from apps.events.models import StaticTriggerType
            from apps.events.tasks import enqueue_static_triggers

            enqueue_static_triggers.delay(self.id, StaticTriggerType.CONVERSATION_END)

    def ad_hoc_bot_message(self, instruction_prompt: str, fail_silently=True, use_experiment: Experiment | None = None):
        """Sends a bot message to this session. The bot message will be crafted using `instruction_prompt` and
        this session's history.

        Parameters:
            instruction_prompt: The instruction prompt for the LLM
            fail_silently: Exceptions will not be suppresed if this is True
            use_experiment: The experiment whose data to use. This is useful for multi-bot setups where we want a
            specific child bot to handle the check-in.
        """
        bot_message = self._bot_prompt_for_user(instruction_prompt=instruction_prompt, use_experiment=use_experiment)
        self.try_send_message(message=bot_message, fail_silently=fail_silently)

    def _bot_prompt_for_user(self, instruction_prompt: str, use_experiment: Experiment | None = None) -> str:
        """Sends the `instruction_prompt` along with the chat history to the LLM to formulate an appropriate prompt
        message. The response from the bot will be saved to the chat history.
        """
        from apps.chat.bots import TopicBot

        topic_bot = TopicBot(self, experiment=use_experiment)
        return topic_bot.process_input(user_input=instruction_prompt, save_input_to_history=False)

    def try_send_message(self, message: str, fail_silently=True):
        """Tries to send a message to this user session as the bot. Note that `message` will be send to the user
        directly. This is not an instruction to the bot.
        """
        from apps.chat.channels import ChannelBase

        try:
            channel = ChannelBase.from_experiment_session(self)
            channel.send_message_to_user(message)
        except Exception as e:
            log.exception(f"Could not send message to experiment session {self.id}. Reason: {e}")
            if not fail_silently:
                raise e

    def get_participant_scheduled_messages(self, as_dict=False, as_timezone: str | None = None):
        """
        Returns all scheduled messages for the associated participant for this session's experiment as well as
        any child experiments in the case where the experiment is a parent

        Parameters:
        as_dict: If True, the data will be returned as an array of dictionaries, otherwise an an array of strings
        timezone: The timezone to use for the dates. Defaults to the active timezone.
        """
        from apps.events.models import ScheduledMessage

        child_experiments = ExperimentRoute.objects.filter(team=self.team, parent=self.experiment).values("child")
        messages = ScheduledMessage.objects.filter(
            Q(experiment=self.experiment) | Q(experiment__in=models.Subquery(child_experiments)),
            participant=self.participant,
            team=self.team,
        ).select_related("action")

        scheduled_messages = []
        as_timezone = as_timezone or timezone.get_current_timezone_name()

        for message in messages:
            next_trigger_date = message.next_trigger_date.astimezone(pytz.timezone(as_timezone))
            if as_dict:
                scheduled_messages.append(
                    {
                        "name": message.name,
                        "frequency": message.frequency,
                        "time_period": message.time_period,
                        "repetitions": message.repetitions,
                        "next_trigger_date": next_trigger_date.isoformat(),
                    }
                )
            else:
                scheduled_messages.append(message.as_string(as_timezone=as_timezone))
        return scheduled_messages

    @cached_property
    def participant_data_from_experiment(self) -> dict:
        try:
            return self.experiment.participant_data.get(participant=self.participant).data
        except ParticipantData.DoesNotExist:
            return {}

    def get_participant_timezone(self):
        participant_data = self.participant_data_from_experiment
        return participant_data.get("timezone")

    def get_participant_data(self, use_participant_tz=False):
        """Returns the participant's data. If `use_participant_tz` is `True`, the dates of the scheduled messages
        will be represented in the timezone that the participant is in if that information is available"""
        participant_data = self.participant_data_from_experiment
        as_timezone = None
        if use_participant_tz:
            as_timezone = self.get_participant_timezone()

        scheduled_messages = self.get_participant_scheduled_messages(as_timezone=as_timezone)
        if scheduled_messages:
            participant_data = {**participant_data, "scheduled_messages": scheduled_messages}
        return participant_data

    def get_participant_data_json(self):
        participant_data = self.participant_data_from_experiment
        scheduled_messages = self.get_participant_scheduled_messages(as_dict=True)
        if scheduled_messages:
            participant_data = {**participant_data, "scheduled_messages": scheduled_messages}
        return json.dumps(participant_data, indent=2)
