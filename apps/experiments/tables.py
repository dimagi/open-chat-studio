from django.conf import settings
from django.template.loader import get_template
from django.urls import reverse
from django_tables2 import columns, tables

from apps.experiments.models import (
    ConsentForm,
    Experiment,
    ExperimentRoute,
    ExperimentSession,
    SafetyLayer,
    SourceMaterial,
    Survey,
)
from apps.generics import actions, chips


class ExperimentTable(tables.Table):
    name = columns.Column(
        orderable=True,
    )
    description = columns.Column(verbose_name="Description")
    owner = columns.Column(accessor="owner__username", verbose_name="Created By")
    type = columns.Column(orderable=False, empty_values=())
    trends = columns.TemplateColumn(
        verbose_name="Trends (last 48h)",
        template_name="table/trends_chart.html",
    )
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
        extra_context={"type": "experiments"},
    )

    class Meta:
        model = Experiment
        fields = ("name",)
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-url": lambda record: (
                record.get_absolute_url() if hasattr(record, "get_absolute_url") else ""
            ),
        }
        orderable = False
        empty_text = "No experiments found."

    def render_name(self, record):
        if record.is_archived:
            return f"{record.name} (archived)"
        return record.name

    def render_type(self, record):
        if record.assistant_id:
            return "Assistant"
        if record.pipeline_id:
            return "Pipeline"
        return "Base LLM"


class SafetyLayerTable(tables.Table):
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="experiments:safety_edit"),
            actions.delete_action(url_name="experiments:safety_delete"),
        ]
    )

    class Meta:
        model = SafetyLayer
        fields = (
            "name",
            "messages_to_review",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No safety layers found."


class SourceMaterialTable(tables.Table):
    owner = columns.Column(accessor="owner__username", verbose_name="Created By")
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="experiments:source_material_edit"),
            actions.delete_action(url_name="experiments:source_material_delete"),
        ]
    )

    class Meta:
        model = SourceMaterial
        fields = (
            "topic",
            "description",
            "owner",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No source material found."


class SurveyTable(tables.Table):
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="experiments:survey_edit"),
            actions.delete_action(url_name="experiments:survey_delete"),
        ]
    )

    class Meta:
        model = Survey
        fields = (
            "name",
            "url",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No surveys found."


class ConsentFormTable(tables.Table):
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="experiments:consent_edit"),
            actions.delete_action(
                url_name="experiments:consent_delete",
                display_condition=lambda request, record: not record.is_default,
            ),
        ]
    )
    capture_identifier = columns.BooleanColumn(yesno="✓,", verbose_name="Capture Identfier")
    is_default = columns.BooleanColumn(yesno="✓,", verbose_name="Is default")

    class Meta:
        model = ConsentForm
        fields = (
            "name",
            "capture_identifier",
            "identifier_label",
            "identifier_type",
            "is_default",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No consent forms found."


def session_chat_url(url_name, request, record, value):
    return reverse(
        url_name, args=[request.team.slug, record.experiment_id, record.get_experiment_version_number(), record.id]
    )


def _show_chat_button(request, record):
    return record.participant.user == request.user and not record.is_complete and record.experiment.is_editable


class ExperimentSessionsTable(tables.Table):
    participant = columns.Column(accessor="participant", verbose_name="Participant", order_by="participant__identifier")
    last_message = columns.Column(accessor="last_message_created_at", verbose_name="Last Message", orderable=True)
    tags = columns.TemplateColumn(verbose_name="Tags", template_name="annotations/tag_ui.html", orderable=False)
    versions = columns.Column(verbose_name="Versions", accessor="experiment_versions", orderable=False)
    state = columns.Column(verbose_name="State", accessor="status", orderable=True)
    remote_id = columns.Column(verbose_name="Remote Id", accessor="participant.remote_id")
    actions = actions.ActionsColumn(
        actions=[
            actions.Action(
                url_name="experiments:experiment_chat_session",
                url_factory=session_chat_url,
                icon_class="fa-solid fa-comment",
                title="Continue Chat",
                display_condition=_show_chat_button,
            ),
            actions.chip_action(
                label="Session Details",
            ),
        ],
        align="right",
    )

    def render_tags(self, record, bound_column):
        template = get_template(bound_column.column.template_name)
        return template.render({"object": record.chat})

    def render_participant(self, record):
        template = get_template("generic/chip.html")
        participant = record.participant
        chip = chips.Chip(
            label=str(participant), url=participant.get_link_to_experiment_data(experiment=record.experiment)
        )
        return template.render({"chip": chip})

    class Meta:
        model = ExperimentSession
        fields = []
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No sessions yet!"


class ExperimentVersionsTable(tables.Table):
    origin = "experiments"
    version_number = columns.TemplateColumn(
        template_name="experiments/components/experiment_version_cell.html", verbose_name="Version Number"
    )
    created_at = columns.Column(verbose_name="Created On", accessor="created_at")
    version_description = columns.Column(verbose_name="Description", default="")
    is_default_version = columns.BooleanColumn(yesno="✓,", verbose_name="Published")
    is_archived = columns.BooleanColumn(yesno="✓,", verbose_name="Archived")
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_version_actions.html",
        verbose_name="",
        attrs={"td": {"class": "overflow-visible"}},
        extra_context={},
    )

    class Meta:
        model = Experiment
        fields = []
        row_attrs = {"class": "text-sm"}
        orderable = False
        empty_text = "No versions yet!"

    def render_created_at(self, record):
        return record.created_at if record.working_version_id else ""


def _get_route_url(url_name, request, record, value):
    return reverse(url_name, args=[request.team.slug, record.parent_id, record.pk])


class ChildExperimentRoutesTable(tables.Table):
    child = actions.chip_column(orderable=True)
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                url_name="experiments:experiment_route_edit",
                url_factory=_get_route_url,
            ),
            actions.delete_action(
                url_name="experiments:experiment_route_delete",
                url_factory=_get_route_url,
            ),
        ]
    )

    class Meta:
        model = ExperimentRoute
        fields = ["child", "keyword", "is_default", "actions"]
        orderable = False
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No routes yet!"


class TerminalBotsTable(ChildExperimentRoutesTable):
    child = actions.chip_column(orderable=True)

    class Meta:
        model = ExperimentRoute
        fields = ["child", "is_default", "actions"]
        orderable = False
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No terminal bots yet!"


class ParentExperimentRoutesTable(tables.Table):
    parent = actions.chip_column(orderable=True)

    class Meta:
        model = ExperimentRoute
        fields = ["parent", "keyword", "is_default"]
        orderable = False
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No routes yet!"
