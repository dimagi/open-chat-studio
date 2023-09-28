import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from apps.chat.models import Chat
from apps.teams.models import BaseTeamModel, Team
from apps.utils.models import BaseModel
from apps.web.meta import absolute_url


class Prompt(BaseModel):
    """
    A prompt - typically the starting point for ChatGPT.
    """

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

    def __str__(self):
        return self.name

    def format(self, input_str):
        if self.input_formatter:
            return self.input_formatter.format(input=input_str)
        else:
            return input_str


class PromptBuilderHistory(BaseModel):
    """
    History entries for the prompt builder
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    history = models.JSONField()

    def __str__(self) -> str:
        return str(self.history)


class SourceMaterial(BaseModel):
    """
    Some Source Material on a particular topic.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    topic = models.CharField(max_length=50)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the source material.")
    material = models.TextField()

    def __str__(self):
        return self.topic


class SafetyLayer(BaseModel):
    REVIEW_CHOICES = (("human", "Human messages"), ("ai", "AI messages"))
    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE)
    messages_to_review = models.CharField(
        choices=REVIEW_CHOICES,
        default="human",
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


class Survey(BaseModel):
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

    def __str__(self):
        return self.name

    def get_link(self, participant, experiment_session):
        participant_public_id = participant.public_id if participant else "[anonymous]"
        return self.url.format(
            participant_id=participant_public_id,
            session_id=experiment_session.public_id,
            experiment_id=experiment_session.experiment.public_id,
        )


class ConsentForm(BaseModel):
    """
    Custom markdown consent form to be used by experiments.
    """

    name = models.CharField(max_length=50)
    consent_text = models.TextField(help_text="Custom markdown text")

    def __str__(self):
        return self.name


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
    name = models.CharField(
        max_length=64, help_text="The name of the synthetic voice, as per the documentation of the service"
    )
    neural = models.BooleanField(default=False, help_text="Indicates whether this voice is a neural voice")
    language = models.CharField(null=False, blank=False, max_length=64, help_text="The language this voice is for")
    gender = models.CharField(
        null=False, blank=False, choices=GENDERS, max_length=14, help_text="The gender of this voice"
    )

    def get_gender(self):
        # This is a bit of a hack to display the gender on the admin screen. Directly calling gender doesn't work
        return self.gender

    class Meta:
        unique_together = ("name", "language", "gender", "neural")

    def __str__(self):
        prefix = "*" if self.neural else ""
        return f"{self.language}, {self.gender}, {prefix}{self.name}"


class NoActivityMessageConfig(BaseModel):
    """Configuration for when the user doesn't respond to the bot's message"""

    message_for_bot = models.CharField(help_text="This message will be sent to the LLM along with the message history")
    name = models.CharField(max_length=64)
    max_pings = models.IntegerField()
    ping_after = models.IntegerField(help_text="The amount of minutes after which to ping the user. Minimum 1.")

    def __str__(self):
        return self.name


class Experiment(BaseModel):
    """
    An experiment combines a chatbot prompt, a safety prompt, and source material.
    Each experiment can be run as a chatbot.
    """

    LLM_CHOICES = (
        ("gpt-3.5-turbo", "GPT 3.5 (Chat GPT)"),
        ("gpt-4", "GPT 4"),
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the experiment.")
    llm = models.CharField(max_length=20, choices=LLM_CHOICES, default="gpt-3.5-turbo")
    temperature = models.FloatField(default=0.7, validators=[MinValueValidator(0), MaxValueValidator(1)])
    chatbot_prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE, related_name="experiments")
    safety_layers = models.ManyToManyField(SafetyLayer, related_name="experiments")
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
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="experiments",
        help_text="If set, this consent form will be used instead of the default one.",
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

    def __str__(self):
        return self.name


class Participant(BaseTeamModel):
    email = models.EmailField()
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)

    def __str__(self):
        return self.email

    class Meta:
        unique_together = ("team", "email")


class SessionStatus(models.TextChoices):
    SETUP = "setup", gettext("Setting Up")
    PENDING = "pending", gettext("Awaiting participant")
    PENDING_PRE_SURVEY = "pending-pre-survey", gettext("Awaiting pre-survey")
    ACTIVE = "active", gettext("Active")
    PENDING_REVIEW = "pending-review", gettext("Awaiting final review.")
    COMPLETE = "complete", gettext("Complete")
    # CANCELLED = "cancelled", gettext("Cancelled")  # not used anywhere yet
    UNKNOWN = "unknown", gettext("Unknown")


class ExperimentSession(BaseModel):
    """
    An individual session, e.g. an instance of a chat with an experiment
    """

    team = models.ForeignKey(Team, verbose_name=gettext("Team"), on_delete=models.CASCADE, null=True, blank=True)

    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=20, choices=SessionStatus.choices, default=SessionStatus.SETUP)
    consent_date = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, help_text="When the experiment (chat) ended.")
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="When the final review was submitted.")

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="sessions")
    chat = models.OneToOneField(Chat, related_name="experiment_session", on_delete=models.CASCADE)
    llm = models.CharField(max_length=20, choices=Experiment.LLM_CHOICES, default="gpt-3.5-turbo")
    seed_task_id = models.CharField(
        max_length=40, blank=True, default="", help_text="System ID of the seed message task, if present."
    )
    no_activity_ping_count = models.IntegerField(default=0, null=False, blank=False)

    class Meta:
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if not hasattr(self, "chat"):
            self.chat = Chat.objects.create(user=self.user, name=self.experiment.name)
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

    def get_platform_name(self) -> str:
        return self.channel_session.experiment_channel.platform_display

    def get_pre_survey_link(self):
        return self.experiment.pre_survey.get_link(self.participant, self)

    def get_post_survey_link(self):
        return self.experiment.post_survey.get_link(self.participant, self)

    def get_channel_session(self):
        if hasattr(self, "channel_session"):
            return self.channel_session
