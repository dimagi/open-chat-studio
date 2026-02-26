from __future__ import annotations

import base64
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from functools import cached_property
from typing import Self
from uuid import uuid4

import markdown
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db import models, transaction
from django.db.models import (
    BooleanField,
    Case,
    Count,
    F,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    When,
    functions,
)
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django_cryptography.fields import encrypt
from field_audit import audit_fields
from field_audit.models import AuditAction, AuditingManager

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments import model_audit_fields
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin, differs
from apps.generics.chips import Chip
from apps.service_providers.tracing import TraceInfo, TracingService
from apps.service_providers.tracing.base import SpanNotificationConfig
from apps.teams.models import BaseTeamModel, Team
from apps.teams.utils import current_team, get_slug_for_team
from apps.trace.models import Trace, TraceStatus
from apps.utils.fields import SanitizedJSONField
from apps.utils.models import BaseModel
from apps.utils.time import seconds_to_human
from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams
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
    def format_pipeline(pipeline) -> str:
        if not pipeline:
            return ""
        name = str(pipeline)
        template = get_template("generic/chip.html")
        url = pipeline.get_absolute_url()
        return template.render({"chip": Chip(label=name, url=url)})

    @staticmethod
    def format_builtin_tools(tools: set) -> str:
        """code_interpreter, file_search -> Code Interpreter, File Search"""
        return ", ".join([tool.replace("_", " ").capitalize() for tool in tools])


class PromptObjectManager(AuditingManager):
    pass


class ExperimentObjectManager(VersionsObjectManagerMixin, AuditingManager):
    def get_default_or_working(self, family_member: Experiment):
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

    def get_version_names(self, team, working_version=None) -> list[str]:
        qs = self.get_queryset().filter(team=team)
        if working_version:
            qs = qs.filter(working_version=working_version)
            nums = qs.order_by("-version_number").values_list("version_number", flat=True).distinct()
            return [f"v{working_version.version_number}"] + [f"v{n}" for n in nums]
        # team-wide distinct version numbers (stable, sorted)
        nums = qs.order_by("-version_number").values_list("version_number", flat=True).distinct()
        return [f"v{n}" for n in nums]


class SourceMaterialObjectManager(VersionsObjectManagerMixin, AuditingManager):
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
    history = SanitizedJSONField()

    def __str__(self) -> str:
        return str(self.history)


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
        return reverse("experiments:source_material_edit", args=[get_slug_for_team(self.team_id), self.id])

    @transaction.atomic()
    def archive(self):
        super().archive()
        self.experiment_set.update(source_material=None, audit_action=AuditAction.AUDIT)

    def _get_version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="topic", raw_value=self.topic),
                VersionField(name="description", raw_value=self.description),
                VersionField(name="material", raw_value=self.material),
            ],
        )


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
        return reverse("experiments:survey_edit", args=[get_slug_for_team(self.team_id), self.id])

    @transaction.atomic()
    def archive(self):
        super().archive()
        self.experiments_pre.update(pre_survey=None, audit_action=AuditAction.AUDIT)
        self.experiments_post.update(post_survey=None, audit_action=AuditAction.AUDIT)

    def _get_version_details(self) -> VersionDetails:
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
        return reverse("experiments:consent_edit", args=[get_slug_for_team(self.team_id), self.id])

    @transaction.atomic()
    def archive(self):
        super().archive()
        consent_form_id = ConsentForm.objects.filter(team=self.team, is_default=True).values("id")[:1]
        self.experiments.update(consent_form_id=Subquery(consent_form_id), audit_action=AuditAction.AUDIT)

    def create_new_version(self, save=True):  # ty: ignore[invalid-method-override]
        new_version = super().create_new_version(save=False)
        new_version.is_default = False
        new_version.save()
        return new_version

    def get_fields_to_exclude(self):
        return super().get_fields_to_exclude() + ["is_default"]

    def _get_version_details(self) -> VersionDetails:
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
    def get_for_team(team: Team, exclude_services=None) -> list[SyntheticVoice]:
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


class BuiltInTools(models.TextChoices):
    WEB_SEARCH = "web-search", gettext("Web Search")
    CODE_EXECUTION = "code-execution", gettext("Code Execution")

    @classmethod
    def get_provider_specific_tools(cls):
        return {
            "openai": [cls.WEB_SEARCH, cls.CODE_EXECUTION],
            "anthropic": [cls.WEB_SEARCH],
            # "google": [cls.WEB_SEARCH, cls.CODE_EXECUTION], commenting it for now until tools work for gemini
        }

    @classmethod
    def choices_for_provider(cls, provider_type: str):
        tools = cls.get_provider_specific_tools().get(provider_type.lower(), [])
        return [(tool, cls(tool).label) for tool in tools]

    @classmethod
    def get_tool_configs_by_provider(cls):
        return {
            "anthropic": {
                cls.WEB_SEARCH: [
                    {
                        "name": "allowed_domains",
                        "type": "expandable_text",
                        "label": "Allowed Domains",
                        "helpText": (
                            "Only search these domains (e.g. example.com or example.com/blog). "
                            "Separate entries with newlines."
                        ),
                    },
                    {
                        "name": "blocked_domains",
                        "type": "expandable_text",
                        "label": "Blocked Domains",
                        "helpText": "Exclude these domains from search. Separate entries with newlines.",
                    },
                ],
            }
        }


class AgentTools(models.TextChoices):
    RECURRING_REMINDER = "recurring-reminder", gettext("Recurring Reminder")
    ONE_OFF_REMINDER = "one-off-reminder", gettext("One-off Reminder")
    DELETE_REMINDER = "delete-reminder", gettext("Delete Reminder")
    MOVE_SCHEDULED_MESSAGE_DATE = "move-scheduled-message-date", gettext("Move Reminder Date")
    UPDATE_PARTICIPANT_DATA = "update-user-data", gettext("Update Participant Data")
    APPEND_TO_PARTICIPANT_DATA = "append-to-participant-data", gettext("Append to Participant Data")
    INCREMENT_COUNTER = "increment-counter", gettext("Increment Counter")
    ATTACH_MEDIA = "attach-media", gettext("Attach Media")
    END_SESSION = "end-session", gettext("End Session")
    SEARCH_INDEX = "file-search", gettext("File Search")
    SEARCH_INDEX_BY_ID = "file-search-by-index", gettext("File Search by index ID")
    SET_SESSION_STATE = "set-session-state", gettext("Set Session State")
    GET_SESSION_STATE = "get-session-state", gettext("Get Session State")
    CALCULATOR = "calculator", gettext("Calculator")

    @classmethod
    def reminder_tools(cls) -> list[Self]:
        return [cls.RECURRING_REMINDER, cls.ONE_OFF_REMINDER, cls.DELETE_REMINDER, cls.MOVE_SCHEDULED_MESSAGE_DATE]  # ty: ignore[invalid-return-type]

    @staticmethod
    def user_tool_choices(include_end_session: bool = True) -> list[tuple]:
        """Returns the set of tools that a user should be able to attach to the bot"""
        excluded_tools = [AgentTools.ATTACH_MEDIA, AgentTools.SEARCH_INDEX, AgentTools.SEARCH_INDEX_BY_ID]
        if not include_end_session:
            excluded_tools.append(AgentTools.END_SESSION)
        return [(tool.value, tool.label) for tool in AgentTools if tool not in excluded_tools]


@audit_fields(*model_audit_fields.EXPERIMENT_FIELDS, audit_special_queryset_writes=True)
class Experiment(BaseTeamModel, VersionsMixin):
    """
    An experiment combines a chatbot prompt, a safety prompt, and source material.
    Each experiment can be run as a chatbot.
    """

    # 0 is a reserved version number, meaning the default version
    DEFAULT_VERSION_NUMBER = 0
    TREND_CACHE_KEY_TEMPLATE = "experiment_trend_data_{experiment_id}"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=128)
    description = models.TextField(null=True, default="", verbose_name="A longer description of the experiment.")  # noqa DJ001
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
    voice_response_behaviour = models.CharField(
        max_length=10,
        choices=VoiceResponseBehaviours.choices,
        default=VoiceResponseBehaviours.RECIPROCAL,
        help_text="This tells the bot when to reply with voice messages",
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
    file_uploads_enabled = models.BooleanField(
        default=False,
        help_text="Enables file attachments in the web chat interface.",
    )
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
        indexes = [
            models.Index(fields=["team", "is_archived", "working_version"]),
        ]

    def __str__(self):
        if self.working_version_id is None:
            return self.name
        return f"{self.name} ({self.version_display})"

    def save(self, *args, **kwargs):
        if self.working_version_id is None and self.is_default_version is True:
            raise ValueError("A working experiment cannot be a default version")
        self._clear_version_cache()
        return super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("chatbots:single_chatbot_home", args=[get_slug_for_team(self.team_id), self.id])

    def get_version(self, version: int) -> Experiment:
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
    def event_triggers(self):
        return [*self.timeout_triggers.all(), *self.static_triggers.all()]

    @property
    def version_display(self) -> str:
        if self.is_working_version:
            return ""
        return f"v{self.version_number}"

    @property
    def trends_cache_key(self) -> str:
        return self.TREND_CACHE_KEY_TEMPLATE.format(experiment_id=self.id)

    @cached_property
    def default_version(self) -> Experiment:
        """Returns the default experiment, or if there is none, the working experiment"""
        return Experiment.objects.get_default_or_working(self)

    def as_chip(self) -> Chip:
        label = self.name
        if self.is_archived:
            label = f"{label} (archived)"
        return Chip(label=label, url=self.get_absolute_url())

    def as_experiment_chip(self) -> Chip:
        """Returns a link to the (legacy) experiment home page"""
        return self.as_chip()

    def as_chatbot_chip(self) -> Chip:
        """Returns a link to the chatbot home page"""
        label = self.name
        if self.is_archived:
            label = f"{label} (archived)"
        url = reverse("chatbots:single_chatbot_home", args=[get_slug_for_team(self.team_id), self.id])
        return Chip(label=label, url=url)

    def get_trend_data(self) -> tuple[list, list]:
        """
        Get the error/success trends across all versions in this experiment's version family.
        Returns two lists: successes and errors, each containing the count of successful and error traces
        for each hour in the last 48 hours.
        """
        days = 2
        to_date = timezone.now()
        from_date = to_date - timezone.timedelta(days=days)

        # Get error counts for each hour bucket
        error_trend = {}
        success_trend = {}

        trace_counts = (
            Trace.objects.filter(
                Q(experiment__working_version_id=self.id) | Q(experiment_id=self.id),
                timestamp__gte=from_date,
                timestamp__lte=to_date,
            )
            .annotate(hour_bucket=functions.TruncHour("timestamp", tzinfo=UTC))
            .values("hour_bucket")
            .annotate(
                error_count=Count(Case(When(status=TraceStatus.ERROR, then=1))),
                success_count=Count(Case(When(status=TraceStatus.SUCCESS, then=1))),
            )
        )

        for trace in trace_counts:
            error_trend[trace["hour_bucket"]] = trace["error_count"]
            success_trend[trace["hour_bucket"]] = trace["success_count"]

        # Create ordered list with zero-filled gaps
        hour_buckets = []
        current = from_date.replace(minute=0, second=0, microsecond=0)
        end = to_date.replace(minute=0, second=0, microsecond=0)

        while current <= end:
            hour_buckets.append(current)
            current += timezone.timedelta(hours=1)

        successes = [success_trend.get(bucket, 0) for bucket in hour_buckets]
        errors = [error_trend.get(bucket, 0) for bucket in hour_buckets]
        return successes, errors

    def traces_url(self) -> str:
        """
        Returns a URL to the traces page, filtered to show only traces for this experiment.
        """
        experiment_filter = ColumnFilterData(column="experiment", operator="any of", value=json.dumps([self.id]))

        versions_to_include = [f"v{n}" for n in range(1, self.version_number + 1)]
        versions_filter = ColumnFilterData(column="versions", operator="any of", value=json.dumps(versions_to_include))

        filter_params = FilterParams(column_filters=[experiment_filter, versions_filter])
        return (
            reverse("trace:home", kwargs={"team_slug": get_slug_for_team(self.team_id)})
            + "?"
            + filter_params.to_query()
        )

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
    def create_new_version(  # ty: ignore[invalid-method-override]
        self,
        version_description: str | None = None,
        make_default: bool = False,
        is_copy: bool = False,
        name: str | None = None,
    ):
        """
        Creates a copy of an experiment as a new version of the original experiment.
        """
        if make_default and is_copy:
            raise ValueError("Cannot make a copy of an experiment the default version")

        version_number = self.version_number
        if not is_copy:
            self.version_number = version_number + 1
            self.save(update_fields=["version_number"])

        # Fetch a new instance so the previous instance reference isn't simply being updated. I am not 100% sure
        # why simply chaing the pk, id and _state.adding wasn't enough.
        new_version = super().create_new_version(save=False, is_copy=is_copy)
        new_version.version_description = version_description or ""
        new_version.public_id = uuid4()
        new_version.version_number = version_number

        if not is_copy and (new_version.version_number == 1 or make_default):
            new_version.is_default_version = True

        if make_default:
            self.versions.filter(is_default_version=True).update(
                is_default_version=False, audit_action=AuditAction.AUDIT
            )
        if is_copy:
            new_version.name = name if name is not None else new_version.name + "_copy"
            new_version.version_number = 1
        new_version.save()

        if not is_copy:
            # nothing to do for copy - just reference the same object in the new copy
            self._copy_attr_to_new_version("source_material", new_version)
            self._copy_attr_to_new_version("consent_form", new_version)
            self._copy_attr_to_new_version("pre_survey", new_version)
            self._copy_attr_to_new_version("post_survey", new_version)

        self._copy_trigger_to_new_version(
            trigger_queryset=self.static_triggers, new_version=new_version, is_copy=is_copy
        )
        self._copy_trigger_to_new_version(
            trigger_queryset=self.timeout_triggers, new_version=new_version, is_copy=is_copy
        )
        self._copy_pipeline_to_new_version(new_version, is_copy)

        return new_version

    def get_fields_to_exclude(self):
        return super().get_fields_to_exclude() + ["is_default_version", "public_id", "version_description"]

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
            if self.pipeline:
                self.pipeline.archive()

    def delete_experiment_channels(self):
        from apps.channels.models import ExperimentChannel

        for channel in ExperimentChannel.objects.filter(experiment_id=self.id):
            channel.soft_delete()

    def _copy_pipeline_to_new_version(self, new_version, is_copy: bool = False):
        if not self.pipeline:
            return
        new_pipeline = self.pipeline.create_new_version(is_copy=is_copy)
        if is_copy:
            new_pipeline.name = new_version.name
            new_pipeline.save(update_fields=["name"])
        new_version.pipeline = new_pipeline
        new_version.save(update_fields=["pipeline"])

    def _copy_attr_to_new_version(self, attr_name, new_version: Experiment):
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

    def _copy_trigger_to_new_version(self, trigger_queryset, new_version, is_copy: bool = False):
        for trigger in trigger_queryset.all():
            trigger.create_new_version(new_experiment=new_version, is_copy=is_copy)

    @property
    def is_public(self) -> bool:
        """
        Whether or not a bot is public depends on the `participant_allowlist`. If it's empty, the bot is public.
        """
        return len(self.participant_allowlist) == 0

    def is_participant_allowed(self, identifier: str):
        return identifier in self.participant_allowlist or self.team.members.filter(email=identifier).exists()

    def _get_version_details(self) -> VersionDetails:
        """
        Returns a `Version` instance representing the experiment version.
        """
        fields = [
            VersionField(group_name="General", name="name", raw_value=self.name),
            VersionField(group_name="General", name="description", raw_value=self.description),
            VersionField(group_name="General", name="seed_message", raw_value=self.seed_message),
            VersionField(
                group_name="General",
                name="allowlist",
                raw_value=self.participant_allowlist,
                to_display=VersionFieldDisplayFormatters.format_array_field,
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
        ]
        if self.pipeline_id:
            fields.append(
                VersionField(
                    group_name="Pipeline",
                    name="pipeline",
                    raw_value=self.pipeline,
                    to_display=VersionFieldDisplayFormatters.format_pipeline,
                ),
            )
        return VersionDetails(
            instance=self,
            fields=fields,
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
        return None


class Participant(BaseTeamModel):
    name = models.CharField(max_length=320, blank=True)
    identifier = models.CharField(max_length=320, blank=True)  # max email length
    public_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    platform = models.CharField(max_length=32)
    remote_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["platform", "identifier"]
        unique_together = [("team", "platform", "identifier")]

    @classmethod
    def create_anonymous(cls, team: Team, platform: str, remote_id: str = "") -> Participant:
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
        if "name" in data and data["name"] is not None:
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

    def get_latest_session(self, experiment: Experiment) -> ExperimentSession:
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
        return reverse("participants:single-participant-home", args=[get_slug_for_team(self.team_id), self.id])

    def get_link_to_experiment_data(self, experiment: Experiment) -> str:
        url = reverse(
            "participants:single-participant-home-with-experiment",
            args=[get_slug_for_team(self.team_id), self.id, experiment.id],
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
            self.get_experiments_queryset(include_archived=True)
            .annotate(
                joined_on=Subquery(joined_on),
                last_message=Subquery(last_message),
            )
            .distinct()
        )

    def get_experiments_queryset(self, include_archived=False):
        """Get the experiments that the participant has interacted with"""
        query = Experiment.objects.get_all() if include_archived else Experiment.objects.all()
        return query.filter(Q(sessions__participant=self) | Q(id__in=Subquery(self.data_set.values("experiment"))))

    def get_data_for_experiment(self, experiment_id) -> dict:
        try:
            return self.data_set.get(experiment_id=experiment_id).data or {}
        except ParticipantData.DoesNotExist:
            return {}

    def get_schedules_for_experiment(
        self, experiment_id, as_dict=False, as_timezone: str | None = None, include_inactive=False
    ):
        """
        Returns all scheduled messages for the associated participant for this session's experiment

        Parameters:
        as_dict: If True, the data will be returned as an array of dictionaries, otherwise an an array of strings
        timezone: The timezone to use for the dates. Defaults to the active timezone.
        """
        from apps.events.models import ScheduledMessage

        messages = (
            ScheduledMessage.objects.filter(
                experiment_id=experiment_id,
                participant=self,
                team=self.team,
            )
            .select_related("action")
            .prefetch_related("attempts")
            .order_by("created_at", "id")
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

    def as_chip(self) -> Chip:
        return Chip(label=self.identifier, url=self.get_absolute_url())


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
    data = encrypt(SanitizedJSONField(default=dict, validators=[validate_json_dict]))
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE)
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

    @classmethod
    def for_chatbots(cls):
        return [cls.ACTIVE.value, cls.COMPLETE.value]


class ExperimentSessionQuerySet(models.QuerySet):
    def annotate_with_message_count(self):
        message_count_subquery = Subquery(
            ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
            .values("chat")
            .annotate(count=Count("id"))
            .values("count")[:1]
        )
        return self.annotate(message_count=message_count_subquery)


class ExperimentSessionObjectManager(models.Manager):
    def get_queryset(self):
        return ExperimentSessionQuerySet(self.model, using=self._db)

    def get_table_queryset(self, team, experiment_id=None):
        from apps.annotations.models import CustomTaggedItem

        queryset = self.get_queryset().filter(team=team)
        if experiment_id:
            queryset = queryset.filter(experiment__id=experiment_id)

        queryset = queryset.select_related("experiment", "participant__user", "chat").prefetch_related(
            Prefetch(
                "chat__tagged_items",
                queryset=CustomTaggedItem.objects.select_related("tag", "user"),
                to_attr="prefetched_tagged_items",
            ),
        )
        return queryset.annotate_with_message_count().order_by(F("last_activity_at").desc(nulls_last=True))


class ExperimentSession(BaseTeamModel):
    """
    An individual session, e.g. an instance of a chat with an experiment
    """

    objects = ExperimentSessionObjectManager()
    external_id = models.CharField(max_length=255, default=uuid.uuid4, unique=True)
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE)
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
        "bot_channels.experimentchannel",
        on_delete=models.SET_NULL,
        related_name="experiment_sessions",
        null=True,
        blank=True,
    )
    state = SanitizedJSONField(default=dict)
    platform = models.CharField(max_length=128, blank=True, null=True)  # noqa: DJ001
    experiment_versions = ArrayField(
        models.PositiveIntegerField(),
        null=True,
        blank=True,
        help_text="Array of unique experiment version numbers seen by this session",
    )
    last_activity_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the last user interaction")
    first_activity_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the first user interaction")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["chat", "team"]), models.Index(fields=["chat", "team", "ended_at"])]

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
                args=[get_slug_for_team(self.team_id), self.experiment.public_id, self.external_id],
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

    @property
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
            "chatbots:chatbot_session_view",
            args=[get_slug_for_team(self.team_id), self.experiment.public_id, self.external_id],
        )

    def end(self, commit: bool = True, trigger_type=None):
        """
        Ends this experiment session

        Args:
            commit: Whether to save the model after setting the ended_at value
            trigger_type: The type of conversation end event to trigger. Leaving this as None will not trigger events.
        Raises:
            ValueError: If trigger_type is specified but commit is not.
        """
        from apps.events.models import StaticTriggerType
        from apps.events.tasks import enqueue_static_triggers

        if trigger_type and not commit:
            raise ValueError("Commit must be True when trigger_type is specified")

        if trigger_type is not None and trigger_type not in StaticTriggerType.end_conversation_types():
            raise ValueError("Only a conversation end trigger type can be used when ending an experiment session.")

        if trigger_type == StaticTriggerType.CONVERSATION_END:
            raise ValueError(
                "Cannot trigger the generic CONVERSATION_END trigger type. Please specify a more specific type."
            )

        self.update_status(SessionStatus.PENDING_REVIEW)

        self.ended_at = timezone.now()
        if commit:
            self.save()
        if commit and trigger_type:
            enqueue_static_triggers.delay(self.id, trigger_type)

    @transaction.atomic()
    def ad_hoc_bot_message(
        self,
        instruction_prompt: str,
        trace_info: TraceInfo,
        fail_silently=True,
        use_experiment: Experiment | None = None,
    ):
        """Sends a bot message to this session and returns the trace data.
        The bot message will be crafted using `instruction_prompt` and
        this session's history.

        Parameters:
            instruction_prompt: The instruction prompt for the LLM
            trace_info: Metadata for adding to the trace
            fail_silently: Exceptions will not be suppresed if this is True
            use_experiment: The experiment whose data to use. This is useful for multi-bot setups where we want a
            specific child bot to handle the check-in.
        """
        trace_service = None
        try:
            with current_team(self.team):
                experiment = use_experiment or self.experiment
                trace_service = TracingService.create_for_experiment(experiment)
                with trace_service.trace_or_span(
                    name=f"{experiment.name} - {trace_info.name}",
                    session=self,
                    inputs={"input": instruction_prompt},
                    metadata=trace_info.metadata,
                    notification_config=SpanNotificationConfig(permissions=["experiments.change_experiment"]),
                ) as span:
                    bot_message = self._bot_prompt_for_user(
                        instruction_prompt, trace_info, use_experiment=use_experiment, trace_service=trace_service
                    )
                    self.try_send_message(message=bot_message)
                    span.set_outputs({"response": bot_message})
                    trace_metadata = trace_service.get_trace_metadata()
                return trace_metadata
        except Exception as e:
            log.exception(f"Could not send message to experiment session {self.id}. Reason: {e}")
            if not fail_silently:
                if trace_service:
                    trace_metadata = trace_service.get_trace_metadata()
                    e.trace_metadata = trace_metadata
                raise e

    def _bot_prompt_for_user(
        self,
        instruction_prompt: str,
        trace_info: TraceInfo,
        trace_service: TracingService,
        use_experiment: Experiment | None = None,
    ) -> str:
        """Sends the `instruction_prompt` along with the chat history to the LLM to formulate an appropriate prompt
        message. The response from the bot will be saved to the chat history.
        """
        from apps.chat.bots import EventBot
        from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager

        experiment = use_experiment or self.experiment
        history_manager = ExperimentHistoryManager(session=self, experiment=experiment, trace_service=trace_service)
        bot = EventBot(self, experiment, trace_info, history_manager)
        return bot.get_user_message(instruction_prompt)

    def try_send_message(self, message: str):
        """Tries to send a message to this user session as the bot. Note that `message` will be send to the user
        directly. This is not an instruction to the bot.
        """
        from apps.chat.channels import ChannelBase

        channel = ChannelBase.from_experiment_session(self)
        channel.send_message_to_user(message)

    @cached_property
    def participant_data_from_experiment(self) -> dict:
        try:
            return self.experiment.participantdata_set.get(participant=self.participant).data
        except ParticipantData.DoesNotExist:
            return {}

    @cached_property
    def experiment_version(self) -> Experiment:
        """Returns the experiment version for this session based on the version stored in chat metadata.
        Falls back to the default version if no specific version is set."""
        version_number = self.get_experiment_version_number()
        return self.experiment.get_version(version_number)

    @cached_property
    def working_experiment(self) -> Experiment:
        """Returns the default experiment, or if there is none, the working experiment"""
        return self.experiment.get_working_version()

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

        if self.experiment.pipeline:
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

    def as_experiment_chip(self) -> Chip:
        """Returns a link to the (legacy) experiment session page"""
        return Chip(label=self.external_id, url=self.get_absolute_url())

    def as_chatbot_chip(self) -> Chip:
        """Returns a link to the chatbot session page"""
        url = reverse(
            "chatbots:chatbot_session_view",
            args=[get_slug_for_team(self.team_id), self.experiment.public_id, self.external_id],
        )
        return Chip(label=self.external_id, url=url)
