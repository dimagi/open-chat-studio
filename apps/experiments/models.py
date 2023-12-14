import uuid

import markdown
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments import model_audit_fields
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel
from apps.web.meta import absolute_url


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


@audit_fields(*model_audit_fields.PROMPT_FIELDS, audit_special_queryset_writes=True)
class Prompt(BaseTeamModel):
    """
    A prompt - typically the starting point for ChatGPT.
    """

    objects = PromptObjectManager()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    description = models.TextField(blank=True, default="", verbose_name="A longer description of what the prompt does.")
    prompt = models.TextField()
    input_formatter = models.TextField(
        blank=True,
        default="",
        help_text="Use the {input} variable somewhere to modify the user input before it reaches the bot. "
        "E.g. 'Safe or unsafe? {input}'",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def format(self, input_str):
        if self.input_formatter:
            return self.input_formatter.format(input=input_str)
        else:
            return input_str


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
    description = models.TextField(null=True, default="", verbose_name="A longer description of the source material.")
    material = models.TextField()

    class Meta:
        ordering = ["topic"]

    def __str__(self):
        return self.topic


@audit_fields(*model_audit_fields.SAFETY_LAYER_FIELDS, audit_special_queryset_writes=True)
class SafetyLayer(BaseTeamModel):
    objects = SafetyLayerObjectManager()
    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE)
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
        return str(self.prompt)


class Survey(BaseTeamModel):
    """
    A survey.
    """

    name = models.CharField(max_length=50)
    url = models.URLField(
        help_text=(
            "Use the {participant_id}, {session_id} and {experiment_id} variables if you want to"
            "include the participant, session and experiment session ids in the url."
        ),
        max_length=500,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_link(self, participant, experiment_session):
        participant_public_id = participant.public_id if participant else "[anonymous]"
        return self.url.format(
            participant_id=participant_public_id,
            session_id=experiment_session.public_id,
            experiment_id=experiment_session.experiment.public_id,
        )


@audit_fields(*model_audit_fields.CONSENT_FORM_FIELDS, audit_special_queryset_writes=True)
class ConsentForm(BaseTeamModel):
    """
    Custom markdown consent form to be used by experiments.
    """

    objects = ConsentFormObjectManager()
    name = models.CharField(max_length=50)
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

    SERVICES = (
        ("AWS", AWS),
        ("Azure", Azure),
    )

    name = models.CharField(
        max_length=64, help_text="The name of the synthetic voice, as per the documentation of the service"
    )
    neural = models.BooleanField(default=False, help_text="Indicates whether this voice is a neural voice")
    language = models.CharField(null=False, blank=False, max_length=64, help_text="The language this voice is for")
    language_code = models.CharField(
        null=False, blank=False, max_length=32, help_text="The language code this voice is for"
    )

    gender = models.CharField(
        null=False, blank=False, choices=GENDERS, max_length=14, help_text="The gender of this voice"
    )
    service = models.CharField(
        null=False, blank=False, choices=SERVICES, max_length=6, help_text="The service this voice is from"
    )

    class Meta:
        ordering = ["name"]
        unique_together = ("name", "language_code", "language", "gender", "neural", "service")

    def get_gender(self):
        # This is a bit of a hack to display the gender on the admin screen. Directly calling gender doesn't work
        return self.gender

    def __str__(self):
        prefix = "*" if self.neural else ""
        return f"{self.language}, {self.gender}: {prefix}{self.name}"


class NoActivityMessageConfig(BaseTeamModel):
    """Configuration for when the user doesn't respond to the bot's message"""

    message_for_bot = models.CharField(help_text="This message will be sent to the LLM along with the message history")
    name = models.CharField(max_length=64)
    max_pings = models.IntegerField()
    ping_after = models.IntegerField(help_text="The amount of minutes after which to ping the user. Minimum 1.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


@audit_fields(*model_audit_fields.EXPERIMENT_FIELDS, audit_special_queryset_writes=True)
class Experiment(BaseTeamModel):
    """
    An experiment combines a chatbot prompt, a safety prompt, and source material.
    Each experiment can be run as a chatbot.
    """

    objects = ExperimentObjectManager()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the experiment.")
    llm_provider = models.ForeignKey(
        "service_providers.LlmProvider", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="LLM Provider"
    )
    llm = models.CharField(
        max_length=20,
        help_text="The LLM model to use.",
        verbose_name="LLM Model",
    )
    temperature = models.FloatField(default=0.7, validators=[MinValueValidator(0), MaxValueValidator(1)])
    chatbot_prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE, related_name="experiments")
    safety_layers = models.ManyToManyField(SafetyLayer, related_name="experiments", blank=True)
    is_active = models.BooleanField(
        default=True, help_text="If unchecked, this experiment will be hidden from everyone besides the owner."
    )
    tools_enabled = models.BooleanField(
        default=False,
        help_text=(
            "If checked, this bot will be able to use prebuilt tools (set reminders etc). This uses more tokens, "
            "so it will cost more."
        ),
    )

    source_material = models.ForeignKey(
        SourceMaterial,
        on_delete=models.CASCADE,
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
        Survey, null=True, blank=True, related_name="experiments_pre", on_delete=models.CASCADE
    )
    post_survey = models.ForeignKey(
        Survey, null=True, blank=True, related_name="experiments_post", on_delete=models.CASCADE
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
    no_activity_config = models.ForeignKey(
        NoActivityMessageConfig,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="This is an experimental feature and might exhibit undesirable behaviour for external channels",
    )
    conversational_consent_enabled = models.BooleanField(
        default=False,
        help_text=(
            "If enabled, the consent form will be sent at the start of a conversation for external channels. Note: "
            "This requires the experiment to have a seed message."
        ),
    )

    class Meta:
        ordering = ["name"]
        permissions = [
            ("invite_participants", "Invite experiment participants"),
            ("download_chats", "Download experiment chats"),
        ]

    def __str__(self):
        return self.name

    def get_chat_model(self):
        service = self.llm_provider.get_llm_service()
        return service.get_chat_model(self.llm, self.temperature)

    def get_absolute_url(self):
        return reverse("experiments:single_experiment_home", args=[self.team.slug, self.id])


class Participant(BaseTeamModel):
    identifier = models.CharField(max_length=320, blank=True)  # max email length
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)

    @property
    def email(self):
        validate_email(self.identifier)
        return self.identifier

    def __str__(self):
        return self.identifier

    class Meta:
        ordering = ["identifier"]
        unique_together = ("team", "identifier")


class SessionStatus(models.TextChoices):
    SETUP = "setup", gettext("Setting Up")
    PENDING = "pending", gettext("Awaiting participant")
    PENDING_PRE_SURVEY = "pending-pre-survey", gettext("Awaiting pre-survey")
    ACTIVE = "active", gettext("Active")
    PENDING_REVIEW = "pending-review", gettext("Awaiting final review.")
    COMPLETE = "complete", gettext("Complete")
    # CANCELLED = "cancelled", gettext("Cancelled")  # not used anywhere yet
    UNKNOWN = "unknown", gettext("Unknown")


class ExperimentSession(BaseTeamModel):
    """
    An individual session, e.g. an instance of a chat with an experiment
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=20, choices=SessionStatus.choices, default=SessionStatus.SETUP)
    consent_date = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, help_text="When the experiment (chat) ended.")
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="When the final review was submitted.")

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="sessions")
    chat = models.OneToOneField(Chat, related_name="experiment_session", on_delete=models.CASCADE)
    llm = models.CharField(max_length=20)
    seed_task_id = models.CharField(
        max_length=40, blank=True, default="", help_text="System ID of the seed message task, if present."
    )
    no_activity_ping_count = models.IntegerField(default=0, null=False, blank=False)
    external_chat_id = models.CharField(null=False)
    experiment_channel = models.ForeignKey(
        "channels.ExperimentChannel",
        on_delete=models.CASCADE,
        related_name="experiment_sessions",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if not hasattr(self, "chat"):
            self.chat = Chat.objects.create(team=self.team, user=self.user, name=self.experiment.name)

        is_web_channel = self.experiment_channel and self.experiment_channel.platform == "web"
        if is_web_channel and self.external_chat_id is None:
            self.external_chat_id = self.chat.id
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
                "experiments:start_experiment_session", args=[self.team.slug, self.experiment.public_id, self.public_id]
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

    def update_status(self, new_status: SessionStatus):
        self.status = new_status
        self.save()
