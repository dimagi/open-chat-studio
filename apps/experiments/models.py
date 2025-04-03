import base64
import logging
import secrets
import uuid
from datetime import datetime
from functools import cached_property
from typing import Self
from uuid import uuid4

import markdown
import pytz
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db import models, transaction
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    F,
    OuterRef,
    Q,
    Subquery,
    UniqueConstraint,
    Value,
    When,
)
from django.db.models.functions import Cast, Concat
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django_cryptography.fields import encrypt
from field_audit import audit_fields
from field_audit.models import AuditAction, AuditingManager

from apps.annotations.models import Tag
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.custom_actions.mixins import CustomActionOperationMixin
from apps.experiments import model_audit_fields
from apps.experiments.versioning import VersionDetails, VersionField, differs
from apps.generics.chips import Chip
from apps.teams.models import BaseTeamModel, Team
from apps.utils.models import BaseModel
from apps.utils.time import seconds_to_human
from apps.web.meta import absolute_url

log = logging.getLogger("ocs.experiments")


class VersionFieldDisplayFormatters:
    """A collection of formatters that are used for displaying version fields"""

    @staticmethod
    def format_tools(tools: set) -> str:
        return ", ".join([AgentTools(tool).label for tool in tools])

    @staticmethod
    def yes_no(value: bool) -> str:
        return "Yes" if value else "No"

    @staticmethod
    def format_array_field(arr: list) -> str:
        return ", ".join([entry for entry in arr])

    @staticmethod
    def format_trigger(triggers) -> str:
        if isinstance(triggers, VersionField):
            triggers = triggers.raw_value
        if not isinstance(triggers, list):
            triggers = [triggers]
        result_strings = []
        for field in triggers:
            if isinstance(field, VersionField):
                field = field.raw_value
            static_trigger = getattr(field, "raw_value", field)
            string = "If"
            if static_trigger.trigger_type == "TimeoutTrigger":
                seconds = seconds_to_human(static_trigger.delay)
                string = f"{string} no response for {seconds}"
            else:
                string = f"{string} {static_trigger.get_type_display().lower()}"
            trigger_action = static_trigger.action.get_action_type_display().lower()
            result_strings.append(f"{string} then {trigger_action}")
        return "; ".join(result_strings) if result_strings else "No triggers found"

    @staticmethod
    def format_route(route) -> str:
        if isinstance(route, VersionField):
            route = route.raw_value
        if isinstance(route, list):
            formatted_routes = []
            for r in route:
                if isinstance(r, VersionField):
                    r = r.raw_value
                if isinstance(r, ExperimentRoute):
                    formatted_routes.append(VersionFieldDisplayFormatters._format_single_route(r))
            return "\n".join(formatted_routes) if formatted_routes else "Invalid route data"
        if isinstance(route, ExperimentRoute):
            return VersionFieldDisplayFormatters._format_single_route(route)
        return "Invalid route data"

    @staticmethod
    def _format_single_route(route) -> str:
        """Formats a single ExperimentRoute"""
        if route.type == ExperimentRouteType.PROCESSOR:
            string = f'Route to "{route.child}" using the "{route.keyword}" keyword.'
            if route.is_default:
                string = f"{string} (default)"
            return string
        elif route.type == ExperimentRouteType.TERMINAL:
            string = f"Use {route.child} as the terminal bot"
        else:
            string = "Unknown route type"
        return string

    @staticmethod
    def format_custom_action_operation(op) -> str:
        action = op.custom_action
        op_details = action.get_operations_by_id().get(op.operation_id)
        return f"{action.name}: {op_details}"

    @staticmethod
    def format_assistant(assistant) -> str:
        if not assistant:
            return ""
        name = assistant.name.split(f" v{assistant.version_number}")[0]
        template = get_template("generic/chip.html")
        url = (
            assistant.get_absolute_url()
            if assistant.is_working_version
            else assistant.working_version.get_absolute_url()
        )
        return template.render({"chip": Chip(label=name, url=url)})

    @staticmethod
    def format_pipeline(pipeline) -> str:
        if not pipeline:
            return ""
        name = pipeline.name.split(f" v{pipeline.version_number}")[0]
        template = get_template("generic/chip.html")
        url = (
            pipeline.get_absolute_url() if pipeline.is_working_version else pipeline.working_version.get_absolute_url()
        )
        return template.render({"chip": Chip(label=name, url=url)})

    @staticmethod
    def format_builtin_tools(tools: set) -> str:
        """code_interpreter, file_search -> Code Interpreter, File Search"""
        return ", ".join([tool.replace("_", " ").capitalize() for tool in tools])


class VersionsObjectManagerMixin:
    def get_all(self):
        """A method to return all experiments whether it is deprecated or not"""
        return super().get_queryset()

    def get_queryset(self):
        query = (
            super()
            .get_queryset()
            .annotate(
                is_version=Case(
                    When(working_version_id__isnull=False, then=True),
                    When(working_version_id__isnull=True, then=False),
                    output_field=BooleanField(),
                )
            )
        )
        try:
            self.model._meta.get_field("is_archived")
        except FieldDoesNotExist:
            pass
        else:
            query = query.filter(is_archived=False)
        return query

    def working_versions_queryset(self):
        """Returns a queryset with only working versions"""
        return self.get_queryset().filter(working_version=None)


class PromptObjectManager(AuditingManager):
    pass


class ExperimentRouteObjectManager(VersionsObjectManagerMixin, models.Manager):
    pass


class ExperimentObjectManager(VersionsObjectManagerMixin, AuditingManager):
    def get_default_or_working(self, family_member: "Experiment"):
        """
        Returns the default version of the family of experiments relating to `family_member` or if there is no default,
        the working experiment.
        """
        if family_member.is_default_version:
            return family_member

        working_version_id = family_member.working_version_id or family_member.id
        experiment = self.filter(
            working_version_id=working_version_id, is_default_version=True, team_id=family_member.team_id
        ).first()
        return experiment if experiment else family_member


class SourceMaterialObjectManager(VersionsObjectManagerMixin, AuditingManager):
    pass


class SafetyLayerObjectManager(VersionsObjectManagerMixin, AuditingManager):
    pass


class ConsentFormObjectManager(VersionsObjectManagerMixin, AuditingManager):
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


class VersionsMixin:
    DEFAULT_EXCLUDED_KEYS = ["id", "created_at", "updated_at", "working_version", "versions", "version_number"]

    @transaction.atomic()
    def create_new_version(self, save=True):
        """
        Creates a new version of this instance and sets the `working_version_id` (if this model supports it) to the
        original instance ID
        """
        working_version_id = self.id
        new_instance = self._meta.model.objects.get(id=working_version_id)
        new_instance.pk = None
        new_instance.id = None
        new_instance._state.adding = True
        if hasattr(new_instance, "working_version_id"):
            new_instance.working_version_id = working_version_id

        if save:
            new_instance.save()
        return new_instance

    @property
    def is_a_version(self):
        """Return whether or not this experiment is a version of an experiment"""
        return self.working_version is not None

    @property
    def is_working_version(self):
        return self.working_version is None

    @property
    def latest_version(self):
        return self.versions.order_by("-created_at").first()

    def get_working_version(self) -> "Experiment":
        """Returns the working version of this experiment family"""
        if self.is_working_version:
            return self
        return self.working_version

    def get_working_version_id(self) -> int:
        return self.working_version_id if self.working_version_id else self.id

    @property
    def has_versions(self):
        return self.versions.exists()

    @property
    def version_family_ids(self) -> list[int]:
        """Returns the ids of records in this version family, including the working version"""
        working_version = self.get_working_version()
        version_family_ids = [working_version.id]
        version_family_ids.extend(working_version.versions.values_list("id", flat=True))
        return version_family_ids

    def get_fields_to_exclude(self):
        """Returns a list of fields that should be excluded when comparing two versions."""
        return self.DEFAULT_EXCLUDED_KEYS

    def archive(self):
        self.is_archived = True
        self.save(update_fields=["is_archived"])

    def is_editable(self) -> bool:
        return not self.is_archived

    def get_version_name(self):
        """Returns version name in form of v + version number, or unreleased if working version."""
        if self.is_working_version:
            return "unreleased"
        return f"v{self.version_number}"

    def get_version_name_list(self):
        """Returns list of version names in form of v + version number including working version."""
        versions_list = list(
            self.versions.annotate(
                friendly_name=Concat(Value("v"), Cast(F("version_number"), output_field=CharField()))
            ).values_list("friendly_name", flat=True)
        )
        versions_list.append(f"v{self.version_number}")
        return versions_list


@audit_fields(*model_audit_fields.SOURCE_MATERIAL_FIELDS, audit_special_queryset_writes=True)
class SourceMaterial(BaseTeamModel, VersionsMixin):
    """
    Some Source Material on a particular topic.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    topic = models.CharField(max_length=50)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the source material.")  # noqa DJ001
    material = models.TextField()
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = SourceMaterialObjectManager()

    class Meta:
        ordering = ["topic"]

    def __str__(self):
        return self.topic

    def get_absolute_url(self):
        return reverse("experiments:source_material_edit", args=[self.team.slug, self.id])

    @transaction.atomic()
    def archive(self):
        super().archive()
        self.experiment_set.update(source_material=None, audit_action=AuditAction.AUDIT)

    @property
    def version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="topic", raw_value=self.topic),
                VersionField(name="description", raw_value=self.description),
                VersionField(name="material", raw_value=self.material),
            ],
        )


@audit_fields(*model_audit_fields.SAFETY_LAYER_FIELDS, audit_special_queryset_writes=True)
class SafetyLayer(BaseTeamModel, VersionsMixin):
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
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = SafetyLayerObjectManager()

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("experiments:safety_edit", args=[self.team.slug, self.id])


class SurveyObjectManager(VersionsObjectManagerMixin, models.Manager):
    def get_queryset(self) -> models.QuerySet:
        return (
            super()
            .get_queryset()
            .annotate(
                is_version=Case(
                    When(working_version_id__isnull=False, then=True),
                    When(working_version_id__isnull=True, then=False),
                    output_field=BooleanField(),
                )
            )
        )


class Survey(BaseTeamModel, VersionsMixin):
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
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = SurveyObjectManager()

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

    @transaction.atomic()
    def archive(self):
        super().archive()
        self.experiments_pre.update(pre_survey=None, audit_action=AuditAction.AUDIT)
        self.experiments_post.update(post_survey=None, audit_action=AuditAction.AUDIT)

    @property
    def version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="name", raw_value=self.name),
                VersionField(name="url", raw_value=self.url),
                VersionField(name="confirmation_text", raw_value=self.confirmation_text),
            ],
        )


@audit_fields(*model_audit_fields.CONSENT_FORM_FIELDS, audit_special_queryset_writes=True)
class ConsentForm(BaseTeamModel, VersionsMixin):
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
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["team_id", "is_default"],
                name="unique_default_consent_form_per_team",
                condition=Q(is_default=True),
            ),
        ]

    @classmethod
    def get_default(cls, team):
        return cls.objects.working_versions_queryset().get(team=team, is_default=True)

    def __str__(self):
        return self.name

    def get_rendered_content(self):
        return markdown.markdown(self.consent_text)

    def get_absolute_url(self):
        return reverse("experiments:consent_edit", args=[self.team.slug, self.id])

    @transaction.atomic()
    def archive(self):
        super().archive()
        consent_form_id = ConsentForm.objects.filter(team=self.team, is_default=True).values("id")[:1]
        self.experiments.update(consent_form_id=Subquery(consent_form_id), audit_action=AuditAction.AUDIT)

    def create_new_version(self, save=True):
        new_version = super().create_new_version(save=False)
        new_version.is_default = False
        new_version.save()
        return new_version

    def get_fields_to_exclude(self):
        return super().get_fields_to_exclude() + ["is_default"]

    @property
    def version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="name", raw_value=self.name),
                VersionField(name="consent_text", raw_value=self.consent_text),
                VersionField(name="capture_identifier", raw_value=self.capture_identifier),
                VersionField(name="identifier_label", raw_value=self.identifier_label),
                VersionField(name="identifier_type", raw_value=self.identifier_type),
                VersionField(name="confirmation_text", raw_value=self.confirmation_text),
            ],
        )


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
    DELETE_REMINDER = "delete-reminder", gettext("Delete Reminder")
    MOVE_SCHEDULED_MESSAGE_DATE = "move-scheduled-message-date", gettext("Move Reminder Date")
    UPDATE_PARTICIPANT_DATA = "update-user-data", gettext("Update Participant Data")
    ATTACH_MEDIA = "attach-media", gettext("Attach Media")

    @classmethod
    def reminder_tools(cls) -> list[Self]:
        return [cls.RECURRING_REMINDER, cls.ONE_OFF_REMINDER, cls.DELETE_REMINDER, cls.MOVE_SCHEDULED_MESSAGE_DATE]

    @staticmethod
    def user_tool_choices() -> list["AgentTools"]:
        """Returns the set of tools that a user should be able to attach to the bot"""
        return [(tool.value, tool.label) for tool in AgentTools if tool != AgentTools.ATTACH_MEDIA]


@audit_fields(*model_audit_fields.EXPERIMENT_FIELDS, audit_special_queryset_writes=True)
class Experiment(BaseTeamModel, VersionsMixin, CustomActionOperationMixin):
    """
    An experiment combines a chatbot prompt, a safety prompt, and source material.
    Each experiment can be run as a chatbot.
    """

    # 0 is a reserved version number, meaning the default version
    DEFAULT_VERSION_NUMBER = 0

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=128)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the experiment.")  # noqa DJ001
    llm_provider = models.ForeignKey(
        "service_providers.LlmProvider", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="LLM Provider"
    )
    llm_provider_model = models.ForeignKey(
        "service_providers.LlmProviderModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The LLM model to use",
        verbose_name="LLM Model",
    )
    assistant = models.ForeignKey(
        "assistants.OpenAiAssistant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="OpenAI Assistant",
    )
    pipeline = models.ForeignKey(
        "pipelines.Pipeline",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Pipeline",
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
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
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
    voice_response_behaviour = models.CharField(
        max_length=10,
        choices=VoiceResponseBehaviours.choices,
        default=VoiceResponseBehaviours.RECIPROCAL,
        help_text="This tells the bot when to reply with voice messages",
    )
    files = models.ManyToManyField("files.File", blank=True)
    children = models.ManyToManyField(
        "Experiment", blank=True, through="ExperimentRoute", symmetrical=False, related_name="parents"
    )
    tools = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    echo_transcript = models.BooleanField(
        default=True,
        help_text=("Whether or not the bot should tell the user what it heard when the user sends voice messages"),
    )
    trace_provider = models.ForeignKey(
        "service_providers.TraceProvider", on_delete=models.SET_NULL, null=True, blank=True
    )
    use_processor_bot_voice = models.BooleanField(default=False)
    participant_allowlist = ArrayField(models.CharField(max_length=128), default=list, blank=True)

    # Versioning fields
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField(default=1)
    is_default_version = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    version_description = models.TextField(
        blank=True,
        default="",
    )
    debug_mode_enabled = models.BooleanField(default=False)
    citations_enabled = models.BooleanField(default=True)
    create_version_task_id = models.CharField(max_length=128, blank=True)
    objects = ExperimentObjectManager()

    class Meta:
        ordering = ["name"]
        permissions = [
            ("invite_participants", "Invite experiment participants"),
            ("download_chats", "Download experiment chats"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default_version", "working_version"],
                condition=Q(is_default_version=True),
                name="unique_default_version_per_experiment",
            ),
            models.UniqueConstraint(
                fields=["version_number", "working_version"],
                condition=Q(working_version__isnull=False),
                name="unique_version_number_per_experiment",
            ),
        ]

    def __str__(self):
        if self.working_version is None:
            return self.name
        return f"{self.name} ({self.version_display})"

    def save(self, *args, **kwargs):
        if self.working_version is None and self.is_default_version is True:
            raise ValueError("A working experiment cannot be a default version")
        return super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("experiments:single_experiment_home", args=[self.team.slug, self.id])

    def get_version(self, version: int) -> "Experiment":
        """
        Returns the version of this experiment family matching `version`. If `version` is 0, the default version is
        returned.
        """
        working_version = self.get_working_version()
        if version == self.DEFAULT_VERSION_NUMBER:
            return working_version.default_version
        elif working_version.version_number == version:
            return working_version
        return working_version.versions.get(version_number=version)

    @property
    def tools_enabled(self):
        return len(self.tools) > 0 or self.custom_action_operations.exists()

    @property
    def event_triggers(self):
        return [*self.timeout_triggers.all(), *self.static_triggers.all()]

    @property
    def version_display(self) -> str:
        if self.is_working_version:
            return ""
        return f"v{self.version_number}"

    @property
    def max_token_limit(self) -> int:
        if self.assistant:
            return self.assistant.llm_provider_model.max_token_limit
        elif self.llm_provider:
            return self.llm_provider_model.max_token_limit

    @cached_property
    def default_version(self) -> "Experiment":
        """Returns the default experiment, or if there is none, the working experiment"""
        return Experiment.objects.get_default_or_working(self)

    def as_chip(self) -> Chip:
        label = self.name
        if self.is_archived:
            label = f"{label} (archived)"
        return Chip(label=label, url=self.get_absolute_url())

    def get_chat_model(self):
        service = self.get_llm_service()
        provider_model_name = self.get_llm_provider_model_name()
        return service.get_chat_model(provider_model_name, self.temperature)

    def get_llm_service(self):
        if self.assistant:
            return self.assistant.get_llm_service()
        elif self.llm_provider:
            return self.llm_provider.get_llm_service()

    def get_llm_provider_model_name(self, raises=True):
        if self.assistant:
            if not self.assistant.llm_provider_model:
                if raises:
                    raise ValueError("llm_provider_model is not set for this Assistant")
                return None
            return self.assistant.llm_provider_model.name
        elif self.llm_provider:
            if not self.llm_provider_model:
                if raises:
                    raise ValueError("llm_provider_model is not set for this Experiment")
                return None
            return self.llm_provider_model.name

    @property
    def trace_service(self):
        if self.trace_provider:
            return self.trace_provider.get_service()

    def get_api_url(self):
        if self.is_working_version:
            return absolute_url(reverse("api:openai-chat-completions", args=[self.public_id]))
        else:
            working_version = self.working_version
            return absolute_url(
                reverse("api:openai-chat-completions-versioned", args=[working_version.public_id, self.version_number])
            )

    @property
    def api_url(self):
        return self.get_api_url()

    @transaction.atomic()
    def create_new_version(self, version_description: str | None = None, make_default: bool = False):
        """
        Creates a copy of an experiment as a new version of the original experiment.
        """
        version_number = self.version_number
        self.version_number = version_number + 1
        self.save(update_fields=["version_number"])

        # Fetch a new instance so the previous instance reference isn't simply being updated. I am not 100% sure
        # why simply chaing the pk, id and _state.adding wasn't enough.
        new_version = super().create_new_version(save=False)
        new_version.version_description = version_description or ""
        new_version.public_id = uuid4()
        new_version.version_number = version_number

        self._copy_attr_to_new_version("source_material", new_version)
        self._copy_attr_to_new_version("consent_form", new_version)
        self._copy_attr_to_new_version("pre_survey", new_version)
        self._copy_attr_to_new_version("post_survey", new_version)

        if new_version.version_number == 1 or make_default:
            new_version.is_default_version = True

        if make_default:
            self.versions.filter(is_default_version=True).update(
                is_default_version=False, audit_action=AuditAction.AUDIT
            )

        new_version.save()

        self._copy_safety_layers_to_new_version(new_version)
        self._copy_routes_to_new_version(new_version)
        self._copy_trigger_to_new_version(trigger_queryset=self.static_triggers, new_version=new_version)
        self._copy_trigger_to_new_version(trigger_queryset=self.timeout_triggers, new_version=new_version)
        self._copy_pipeline_to_new_version(new_version)
        self._copy_custom_action_operations_to_new_version(new_experiment=new_version)
        self._copy_assistant_to_new_version(new_version)

        new_version.files.set(self.files.all())
        return new_version

    def get_fields_to_exclude(self):
        return super().get_fields_to_exclude() + ["is_default_version", "public_id", "version_description"]

    def compare_with_latest(self):
        """
        Returns a boolean if the experiment differs from the lastest version
        """
        version = self.version_details
        if prev_version := self.latest_version:
            version.compare(prev_version.version_details, early_abort=True)
        return version.fields_changed

    @transaction.atomic()
    def archive(self):
        """
        Archive the experiment and all versions in the case where this is the working version. The linked assistant and
        pipeline for the working version should not be archived.
        """
        super().archive()
        self.static_triggers.update(is_archived=True)

        if self.is_working_version:
            self.delete_experiment_channels()
            self.versions.update(is_archived=True, audit_action=AuditAction.AUDIT)
            self.scheduled_messages.all().delete()
        else:
            if self.assistant:
                self.assistant.archive()
            elif self.pipeline:
                self.pipeline.archive()

    def delete_experiment_channels(self):
        from apps.channels.models import ExperimentChannel

        for channel in ExperimentChannel.objects.filter(experiment_id=self.id):
            channel.soft_delete()

    def _copy_pipeline_to_new_version(self, new_version):
        if not self.pipeline:
            return
        new_version.pipeline = self.pipeline.create_new_version()
        new_version.save(update_fields=["pipeline"])

    def _copy_assistant_to_new_version(self, new_version):
        if not self.assistant:
            return
        new_version.assistant = self.assistant.create_new_version()
        new_version.save(update_fields=["assistant"])

    def _copy_attr_to_new_version(self, attr_name, new_version: "Experiment"):
        """Copies the attribute `attr_name` to the new version by creating a new version of the related record and
        linking that to `new_version`

        If the related field's version matches the current value, link it to the new experiment version; otherwise,
        create a new version of it.
        Q: Why?
        A: When a new experiment version is created, the a new version is also created for the related field. If no
        new changes was made to this new version by the time we want to create another version of the experiment, it
        would make sense to add the already versioned related field to the versioned experiment instead of creating yet
        another version of it.
        """
        attr_instance = getattr(self, attr_name)
        if not attr_instance:
            return

        latest_attr_version = attr_instance.latest_version

        if latest_attr_version and not differs(
            attr_instance, latest_attr_version, exclude_model_fields=latest_attr_version.get_fields_to_exclude()
        ):
            setattr(new_version, attr_name, latest_attr_version)
        else:
            setattr(new_version, attr_name, attr_instance.create_new_version())

    def _copy_safety_layers_to_new_version(self, new_version: "Experiment"):
        duplicated_layers = []
        for layer in self.safety_layers.all():
            duplicated_layers.append(layer.create_new_version())
        new_version.safety_layers.set(duplicated_layers)

    def _copy_routes_to_new_version(self, new_version: "Experiment"):
        """
        This copies the experiment routes where this experiment is the parent and sets the new parent to the new
        version.
        """
        for route in self.child_links.all():
            route.create_new_version(new_version)

    def _copy_trigger_to_new_version(self, trigger_queryset, new_version):
        for trigger in trigger_queryset.all():
            trigger.create_new_version(new_experiment=new_version)

    @property
    def is_public(self) -> bool:
        """
        Whether or not a bot is public depends on the `participant_allowlist`. If it's empty, the bot is public.
        """
        return len(self.participant_allowlist) == 0

    def is_participant_allowed(self, identifier: str):
        return identifier in self.participant_allowlist or self.team.members.filter(email=identifier).exists()

    @property
    def version_details(self) -> VersionDetails:
        """
        Returns a `Version` instance representing the experiment version.
        """
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name="General", name="name", raw_value=self.name),
                VersionField(group_name="General", name="description", raw_value=self.description),
                VersionField(group_name="General", name="seed_message", raw_value=self.seed_message),
                VersionField(
                    group_name="General",
                    name="allowlist",
                    raw_value=self.participant_allowlist,
                    to_display=VersionFieldDisplayFormatters.format_array_field,
                ),
                # Language Model
                VersionField(group_name="Language Model", name="prompt_text", raw_value=self.prompt_text),
                VersionField(group_name="Language Model", name="llm_provider_model", raw_value=self.llm_provider_model),
                VersionField(group_name="Language Model", name="llm_provider", raw_value=self.llm_provider),
                VersionField(group_name="Language Model", name="temperature", raw_value=self.temperature),
                # Safety
                VersionField(
                    group_name="Safety",
                    name="safety_layers",
                    queryset=self.safety_layers,
                ),
                VersionField(
                    group_name="Safety",
                    name="safety_violation_emails",
                    raw_value=", ".join(self.safety_violation_notification_emails),
                ),
                VersionField(
                    group_name="Safety",
                    name="input_formatter",
                    raw_value=self.input_formatter,
                ),
                # Consent
                VersionField(group_name="Consent", name="consent_form", raw_value=self.consent_form),
                VersionField(
                    group_name="Consent",
                    name="conversational_consent_enabled",
                    raw_value=self.conversational_consent_enabled,
                    to_display=VersionFieldDisplayFormatters.yes_no,
                ),
                # Surveys
                VersionField(group_name="Surveys", name="pre-survey", raw_value=self.pre_survey),
                VersionField(group_name="Surveys", name="post_survey", raw_value=self.post_survey),
                # Voice
                VersionField(group_name="Voice", name="voice_provider", raw_value=self.voice_provider),
                VersionField(group_name="Voice", name="synthetic_voice", raw_value=self.synthetic_voice),
                VersionField(
                    group_name="Voice",
                    name="voice_response_behaviour",
                    raw_value=VoiceResponseBehaviours(self.voice_response_behaviour).label,
                ),
                VersionField(
                    group_name="Voice",
                    name="echo_transcript",
                    raw_value=self.echo_transcript,
                    to_display=VersionFieldDisplayFormatters.yes_no,
                ),
                VersionField(
                    group_name="Voice",
                    name="use_processor_bot_voice",
                    raw_value=self.use_processor_bot_voice,
                    to_display=VersionFieldDisplayFormatters.yes_no,
                ),
                # Source material
                VersionField(
                    group_name="Source Material",
                    name="source_material",
                    raw_value=self.source_material,
                ),
                # Tools
                VersionField(
                    group_name="Tools",
                    name="tools",
                    raw_value=set(self.tools),
                    to_display=VersionFieldDisplayFormatters.format_tools,
                ),
                VersionField(
                    group_name="Tools",
                    name="custom_actions",
                    queryset=self.get_custom_action_operations(),
                    to_display=VersionFieldDisplayFormatters.format_custom_action_operation,
                ),
                VersionField(
                    group_name="Assistant",
                    name="assistant",
                    raw_value=self.assistant,
                    to_display=VersionFieldDisplayFormatters.format_assistant,
                ),
                VersionField(
                    group_name="Pipeline",
                    name="pipeline",
                    raw_value=self.pipeline,
                    to_display=VersionFieldDisplayFormatters.format_pipeline,
                ),
                VersionField(group_name="Tracing", name="tracing_provider", raw_value=self.trace_provider),
                # Triggers
                VersionField(
                    group_name="Triggers",
                    name="static_triggers",
                    queryset=self.static_triggers.all(),
                    to_display=VersionFieldDisplayFormatters.format_trigger,
                ),
                VersionField(
                    group_name="Triggers",
                    name="timeout_triggers",
                    queryset=self.timeout_triggers.all(),
                    to_display=VersionFieldDisplayFormatters.format_trigger,
                ),
                # Routing
                VersionField(
                    group_name="Routing",
                    name="routes",
                    queryset=self.child_links.filter(type=ExperimentRouteType.PROCESSOR),
                    to_display=VersionFieldDisplayFormatters.format_route,
                ),
                VersionField(
                    group_name="Routing",
                    name="terminal_bot",
                    queryset=self.child_links.filter(type=ExperimentRouteType.TERMINAL),
                    to_display=VersionFieldDisplayFormatters.format_route,
                ),
            ],
        )

    def get_assistant(self):
        """
        Retrieves the assistant associated with the current instance.

        This method attempts to find an assistant node within the pipeline associated with the current instance.
        - If an assistant node is found, it retrieves the assistant ID from the node's parameters and returns the
        corresponding OpenAiAssistant object.
        - If no assistant node is found or if the pipeline is not set, it returns the default assistant associated with
        the instance.
        """
        from apps.assistants.models import OpenAiAssistant
        from apps.pipelines.models import Node
        from apps.pipelines.nodes.nodes import AssistantNode

        if self.pipeline:
            node_name = AssistantNode.__name__
            # TODO: What about multiple assistant nodes?
            assistant_id = (
                Node.objects.filter(type=node_name, pipeline=self.pipeline, params__assistant_id__isnull=False)
                .values_list("params__assistant_id", flat=True)
                .first()
            )
            if assistant_id:
                return OpenAiAssistant.objects.get(id=assistant_id)
        return self.assistant


class ExperimentRouteType(models.TextChoices):
    PROCESSOR = "processor"
    TERMINAL = "terminal"


class ExperimentRoute(BaseTeamModel, VersionsMixin):
    """
    Through model for Experiment.children routes.
    """

    parent = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="child_links")
    child = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="parent_links")
    keyword = models.SlugField(max_length=128)
    is_default = models.BooleanField(default=False)
    type = models.CharField(choices=ExperimentRouteType.choices, max_length=64, default=ExperimentRouteType.PROCESSOR)
    condition = models.CharField(max_length=64, blank=True)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = ExperimentRouteObjectManager()

    @classmethod
    def eligible_children(cls, team: Team, parent: Experiment | None = None):
        """
        Returns a list of experiments that fit the following criteria:
        - They are not the same as the parent
        - they are not parents
        - they are not not children of the current experiment
        - they are not part of the current experiment's version family
        """
        parent_ids = cls.objects.filter(team=team).values_list("parent_id", flat=True).distinct()

        if parent:
            child_ids = cls.objects.filter(parent=parent).values_list("child_id", flat=True)
            eligible_experiments = (
                Experiment.objects.filter(team=team)
                .exclude(id__in=child_ids)
                .exclude(id__in=parent_ids)
                .exclude(id=parent.id)
                .exclude(working_version_id=parent.id)
            )
        else:
            eligible_experiments = Experiment.objects.filter(team=team).exclude(id__in=parent_ids)

        return eligible_experiments.filter(working_version_id=None)

    @transaction.atomic()
    def create_new_version(self, new_parent: Experiment) -> "ExperimentRoute":
        """
        Strategy:
        - If the current child doesn't have any versions, create a new child version for the new route version
        - If the current child have versions and there are changes between the current child and its latest version,
            a new child version should be created for the new route version
        - Alternatively, if there are were changes made since the last child version were made, use the latest version
            for the new route version
        """

        new_route = super().create_new_version(save=False)
        new_route.parent = new_parent
        new_route.child = None
        working_child = self.child

        if latest_child_version := working_child.latest_version:
            # Compare experimens using their `version` instances for a comprehensive comparison
            current_version_details: VersionDetails = working_child.version_details
            current_version_details.compare(latest_child_version.version_details)

            if current_version_details.fields_changed:
                fields_changed = [f.name for f in current_version_details.fields if f.changed]
                description = self._generate_version_description(fields_changed)
                new_route.child = working_child.create_new_version(version_description=description)
            else:
                new_route.child = latest_child_version
        else:
            new_route.child = working_child.create_new_version()

        new_route.save()
        return new_route

    def _generate_version_description(self, changed_fields: set | None = None) -> str:
        description = "Auto created when the parent experiment was versioned"
        if changed_fields:
            changed_fields = ",".join(changed_fields)
            description = f"{description} since {changed_fields} changed."
        return description

    @property
    def version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name=self.keyword, name="keyword", raw_value=self.keyword),
                VersionField(group_name=self.keyword, name="child", raw_value=self.child),
            ],
        )

    class Meta:
        constraints = [
            UniqueConstraint(fields=["parent", "child"], condition=Q(is_archived=False), name="unique_parent_child"),
            UniqueConstraint(
                fields=["parent", "keyword", "condition"],
                condition=Q(is_archived=False),
                name="unique_parent_keyword_condition",
            ),
        ]


class Participant(BaseTeamModel):
    name = models.CharField(max_length=320, blank=True)
    identifier = models.CharField(max_length=320, blank=True)  # max email length
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    platform = models.CharField(max_length=32)

    class Meta:
        ordering = ["platform", "identifier"]
        unique_together = [("team", "platform", "identifier")]

    @classmethod
    def create_anonymous(cls, team: Team, platform: str) -> "Participant":
        public_id = str(uuid.uuid4())
        return cls.objects.create(
            team=team, platform=platform, identifier=f"anon:{public_id}", public_id=public_id, name="Anonymous"
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
        experiment = self.get_experiments_for_display().first()
        if experiment:
            return self.get_link_to_experiment_data(experiment)
        return reverse("participants:single-participant-home", args=[self.team.slug, self.id])

    def get_link_to_experiment_data(self, experiment: Experiment) -> str:
        url = reverse(
            "participants:single-participant-home-with-experiment", args=[self.team.slug, self.id, experiment.id]
        )
        return f"{url}#{experiment.id}"

    def get_experiments_for_display(self):
        """Used by the html templates to display various stats about the participant's participation."""
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
            return self.data_set.get(experiment=experiment).data
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

        child_experiments = ExperimentRoute.objects.filter(team=self.team, parent=experiment).values("child")
        messages = (
            ScheduledMessage.objects.filter(
                Q(experiment=experiment) | Q(experiment__in=models.Subquery(child_experiments)),
                participant=self,
                team=self.team,
            )
            .select_related("action")
            .order_by("created_at")
        )
        if not include_inactive:
            messages = messages.filter(is_complete=False, cancelled_at=None)

        scheduled_messages = []
        for message in messages:
            if as_dict:
                next_trigger_date = message.next_trigger_date
                last_triggered_at = message.last_triggered_at
                if as_timezone:
                    next_trigger_date = next_trigger_date.astimezone(pytz.timezone(as_timezone))
                    if last_triggered_at:
                        last_triggered_at = last_triggered_at.astimezone(pytz.timezone(as_timezone))
                scheduled_messages.append(message.as_dict(as_timezone=as_timezone))
            else:
                scheduled_messages.append(message.as_string(as_timezone=as_timezone))
        return scheduled_messages

    @transaction.atomic()
    def update_memory(self, data: dict, experiment: Experiment):
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


class ParticipantDataObjectManager(models.Manager):
    def for_experiment(self, experiment: Experiment):
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
    data = encrypt(models.JSONField(default=dict, validators=[validate_json_dict]))
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE)
    system_metadata = models.JSONField(default=dict)
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
        indexes = [
            models.Index(fields=["experiment"]),
        ]
        # A bot cannot have a link to multiple data entries for the same Participant
        # Multiple bots can have a link to the same ParticipantData record
        # A participant can have many participant data records
        unique_together = ("participant", "experiment")


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
    state = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ExperimentSession(id={self.external_id})"

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

    def get_participant_chip(self) -> Chip:
        if self.participant:
            return Chip(
                label=str(self.participant),
                url=self.participant.get_link_to_experiment_data(experiment=self.experiment),
            )
        else:
            return Chip(label="Anonymous", url="")

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
        if not self.experiment_channel:
            return self.participant.get_platform_display()
        return self.experiment_channel.get_platform_display()

    def get_pre_survey_link(self, experiment_version: Experiment):
        return experiment_version.pre_survey.get_link(self.participant, self)

    def get_post_survey_link(self, experiment_version: Experiment):
        return experiment_version.post_survey.get_link(self.participant, self)

    def is_stale(self) -> bool:
        """A Channel Session is considered stale if the experiment that the channel points to differs from the
        one that the experiment session points to. This will happen when the user repurposes the channel to point
        to another experiment."""
        from apps.channels.models import ChannelPlatform

        if self.experiment_channel.platform in ChannelPlatform.team_global_platforms():
            return False
        return self.experiment_channel.experiment != self.experiment

    def is_complete(self):
        return self.status == SessionStatus.COMPLETE

    def update_status(self, new_status: SessionStatus, commit: bool = True):
        if self.status == new_status:
            return

        self.status = new_status
        if commit:
            self.save()

    def get_absolute_url(self):
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
        from apps.chat.bots import EventBot

        bot = EventBot(self, use_experiment)
        message = bot.get_user_message(instruction_prompt)
        chat_message = ChatMessage.objects.create(chat=self.chat, message_type=ChatMessageType.AI, content=message)
        chat_message.add_version_tag(
            version_number=bot.experiment.version_number, is_a_version=bot.experiment.is_a_version
        )
        return message

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

    @cached_property
    def participant_data_from_experiment(self) -> dict:
        try:
            return self.experiment.participantdata_set.get(participant=self.participant).data
        except ParticipantData.DoesNotExist:
            return {}

    @cached_property
    def experiment_version(self) -> Experiment:
        """Returns the default experiment, or if there is none, the working experiment"""
        return self.experiment.default_version

    @cached_property
    def working_experiment(self) -> Experiment:
        """Returns the default experiment, or if there is none, the working experiment"""
        return self.experiment.get_working_version()

    @property
    def experiment_version_for_display(self):
        version_tags = list(
            Tag.objects.filter(chatmessage__chat=self.chat, category=Chat.MetadataKeys.EXPERIMENT_VERSION)
            .order_by("name")
            .values_list("name", flat=True)
            .distinct()
        )
        if not version_tags:
            return ""

        return ", ".join(version_tags)

    def get_experiment_version_number(self) -> int:
        """
        Returns the version that is being chatted to. If it's the default version, return 0 which is the default
        experiment's version number
        """
        return self.chat.metadata.get(Chat.MetadataKeys.EXPERIMENT_VERSION, Experiment.DEFAULT_VERSION_NUMBER)

    def requires_participant_data(self) -> bool:
        """Determines if participant data is required for this session"""
        from apps.assistants.models import OpenAiAssistant
        from apps.pipelines.nodes.nodes import AssistantNode, LLMResponseWithPrompt, RouterNode

        if self.experiment.assistant:
            return "{participant_data}" in self.experiment.assistant.instructions
        elif self.experiment.pipeline:
            assistant_ids = self.experiment.pipeline.get_node_param_values(AssistantNode, param_name="assistant_id")
            results = OpenAiAssistant.objects.filter(
                id__in=assistant_ids, instructions__contains="{participant_data}"
            ).exists()
            if results:
                return True
            llm_prompts = self.experiment.pipeline.get_node_param_values(LLMResponseWithPrompt, param_name="prompt")
            router_prompts = self.experiment.pipeline.get_node_param_values(RouterNode, param_name="prompt")
            prompts = llm_prompts + router_prompts
            return bool([prompt for prompt in prompts if "{participant_data}" in prompt])
        else:
            return "{participant_data}" in self.experiment.prompt_text
