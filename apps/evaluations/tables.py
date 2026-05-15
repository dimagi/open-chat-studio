from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django_tables2 import TemplateColumn, columns, tables

from apps.chatbots.version_resolver import VersionSelectionRule
from apps.evaluations.models import (
    DatasetAutoPopulationRule,
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMode,
    EvaluationRun,
    Evaluator,
)
from apps.evaluations.utils import get_evaluator_type_display
from apps.experiments.models import ExperimentSession
from apps.generics import actions
from apps.generics.actions import chip_action
from apps.generics.tables import ArrayColumn, TemplateColumnWithCustomHeader
from apps.teams.utils import get_slug_for_team


class EvaluationConfigTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    generation_chatbot = columns.Column(
        verbose_name="Generation Chatbot",
        orderable=False,
        empty_values=(),  # Don't show "—" for empty values, let render method handle it
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:edit"),
            actions.Action(
                url_name="evaluations:create_evaluation_preview",
                url_factory=lambda url_name, request, record, value: reverse(
                    url_name, args=[request.team.slug, record.id]
                ),
                icon_class="fa-solid fa-eye",
                title="Preview",
            ),
            actions.Action(
                url_name="evaluations:create_evaluation_run",
                url_factory=lambda url_name, request, record, value: reverse(
                    url_name, args=[request.team.slug, record.id]
                ),
                icon_class="fa-solid fa-play",
                title="Run",
            ),
        ]
    )

    def render_evaluators(self, value, record):
        """Render the evaluators column with icons and labels in an unordered list."""
        if not value.exists():
            return "—"

        rows = []
        for evaluator in value.all():
            type_info = get_evaluator_type_display(evaluator.type)
            icon_class = type_info.get("icon") or ""
            label = type_info.get("label") or ""
            # icon_class and label come from get_evaluator_type_display (developer-controlled),
            # safe to interpolate. evaluator.name is user-controlled — format_html escapes it.
            rows.append((icon_class, evaluator.name, label))

        items = format_html_join(
            "",
            "<li>{}{} ({})</li>",
            ((format_html('<i class="fa {}"></i> ', icon) if icon else "", name, label) for icon, name, label in rows),
        )
        return format_html('<ul class="list-disc list-inside">{}</ul>', items)

    def render_generation_chatbot(self, record):
        if not record.base_experiment:
            return "—"
        if record.version_selection_type == VersionSelectionRule.LATEST_WORKING:
            return f"{record.base_experiment.name} (Latest Working)"
        elif record.version_selection_type == VersionSelectionRule.LATEST_PUBLISHED:
            return f"{record.base_experiment.name} (Latest Published)"
        elif record.experiment_version:
            version_display = (
                f" ({record.experiment_version.version_display})" if record.experiment_version.version_display else ""
            )
            return f"{record.experiment_version.name}{version_display}"
        return "—"

    class Meta:
        model = EvaluationConfig
        fields = (
            "name",
            "evaluators",
            "dataset",
            "generation_chatbot",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No evaluation configurations found."


class EvaluationRunTable(tables.Table):
    created_at = columns.DateTimeColumn(
        verbose_name="Created",
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )

    status = TemplateColumn(
        template_name="evaluations/evaluation_run_status_column.html", verbose_name="Status", orderable=False
    )

    type = TemplateColumn(
        # Use `.all|length` (not `.count`) so the prefetch cache wired up by
        # EvaluationRunTableView.get_queryset is used; `.count` would issue a
        # fresh query per row (N+1).
        template_code=(
            "{% if record.type == 'delta' %}"
            "<span class='badge badge-info'>delta · {{ record.scoped_messages.all|length }}</span>"
            "{% elif record.type == 'preview' %}"
            "<span class='badge'>preview</span>"
            "{% else %}"
            "<span class='badge badge-ghost'>full</span>"
            "{% endif %}"
        ),
        verbose_name="Type",
        orderable=False,
    )

    results = columns.Column(accessor="results__count", verbose_name="Result count", orderable=False)

    actions = actions.ActionsColumn(
        actions=[
            actions.Action(
                url_name="evaluations:evaluation_run_download",
                url_factory=lambda url_name, request, record, _: reverse(
                    url_name, args=[request.team.slug, record.config_id, record.id]
                ),
                icon_class="fa-solid fa-download",
                title="Download CSV",
                enabled_condition=lambda _, record: record.status == "completed",
            ),
        ]
    )

    class Meta:
        model = EvaluationRun
        fields = ("created_at", "type", "status", "finished_at", "results", "actions")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No runs found."


class EvaluatorTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    type = columns.Column(
        verbose_name="Type",
        orderable=True,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:evaluator_edit"),
            actions.AjaxAction(
                "evaluations:evaluator_delete",
                title="Delete",
                icon_class="fa-solid fa-trash",
                confirm_message="This will permanently delete the evaluator. Are you sure?",
                hx_method="delete",
            ),
        ]
    )

    def render_type(self, value, record):
        """Render the type column with icon and label."""
        type_info = get_evaluator_type_display(value)
        icon_html = f'<i class="fa {type_info["icon"]}"></i> ' if type_info["icon"] else ""
        return mark_safe(f"{icon_html}{type_info['label']}")

    class Meta:
        model = Evaluator
        fields = (
            "name",
            "type",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No evaluators found."


class EvaluationDatasetTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    items = columns.Column(accessor="message_count", verbose_name="Items", orderable=False)

    def render_items(self, value, record):
        label = "session" if record.evaluation_mode == EvaluationMode.SESSION else "message"
        if value != 1:
            label += "s"
        return f"{value} {label}"

    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:dataset_edit"),
            actions.Action(
                url_name="evaluations:dataset_download",
                url_factory=lambda url_name, request, record, _: reverse(url_name, args=[request.team.slug, record.id]),
                icon_class="fa-solid fa-download",
                title="Download CSV",
            ),
            actions.AjaxAction(
                "evaluations:dataset_delete",
                title="Delete",
                icon_class="fa-solid fa-trash",
                confirm_message="This will permanently delete the dataset and all its messages. Are you sure?",
                hx_method="delete",
            ),
        ]
    )

    class Meta:
        model = EvaluationDataset
        fields = (
            "name",
            "items",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No datasets found."


def _chip_session_url_factory(_, request, record, __):
    return reverse(
        "chatbots:chatbot_session_view",
        args=[get_slug_for_team(record.team_id), record.experiment.public_id, record.external_id],
    )


class EvaluationSessionsSelectionTable(tables.Table):
    selection = TemplateColumnWithCustomHeader(
        template_name="evaluations/session_checkbox.html",
        verbose_name="All",
        orderable=False,
        extra_context={
            "css_class": "checkbox checkbox-primary session-checkbox",
            "js_function": "updateSelectedSessions()",
        },
        header_template="evaluations/session_checkbox.html",
        header_context={
            "help_content": "Include all messages from these sessions in the dataset",
            "js_function": "toggleSelectedSessions()",
            "css_class": "checkbox checkbox-primary session-checkbox",
        },
    )
    clone_filtered_only = TemplateColumnWithCustomHeader(
        template_name="evaluations/session_checkbox.html",
        verbose_name="Filtered",
        orderable=False,
        attrs={"th": {"class": "col-filtered-only"}, "td": {"class": "col-filtered-only"}},
        extra_context={
            "css_class": "checkbox checkbox-secondary filter-checkbox",
            "js_function": "updateFilteredSessions()",
        },
        header_template="evaluations/session_checkbox.html",
        header_context={
            "help_content": "Include only messages matching the current filters in the dataset",
            "js_function": "toggleFilteredSessions()",
            "css_class": "checkbox checkbox-secondary filter-checkbox",
        },
    )
    experiment = columns.Column(accessor="experiment", verbose_name="Experiment", order_by="experiment__name")
    participant = columns.Column(accessor="participant", verbose_name="Participant", order_by="participant__identifier")
    last_message = columns.Column(accessor="last_activity_at", verbose_name="Last Message", orderable=True)
    versions = ArrayColumn(verbose_name="Versions", accessor="experiment_versions", orderable=False)
    message_count = columns.Column(accessor="message_count", verbose_name="Messages", orderable=False)
    session = actions.ActionsColumn(
        actions=[
            chip_action(
                label="View Session",
                url_factory=_chip_session_url_factory,
                open_url_in_new_tab=True,
            ),
        ],
        orderable=True,
    )

    class Meta:
        model = ExperimentSession
        fields = []
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-target": "_blank",
        }
        attrs = {"class": "table w-full"}
        orderable = False
        empty_text = "No sessions available for selection."


class DatasetAutoPopulationRuleTable(tables.Table):
    source_experiment = columns.Column(verbose_name="Source chatbot", orderable=False)
    is_enabled = columns.BooleanColumn(verbose_name="Enabled", orderable=False, yesno="Yes,No")
    last_run_at = columns.DateTimeColumn(verbose_name="Last run", orderable=False)
    last_run_status = columns.Column(
        accessor="get_last_run_status_display",
        verbose_name="Status",
        orderable=False,
    )
    last_error = columns.Column(verbose_name="Last error", orderable=False)

    def render_last_error(self, value):
        if not value:
            return "—"
        return format_html('<span class="text-error">{}</span>', value[:60])

    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                url_name="evaluations:auto_population_rule_edit",
                url_factory=lambda url_name, request, record, _: reverse(
                    url_name, args=[request.team.slug, record.dataset_id, record.id]
                ),
            ),
            actions.AjaxAction(
                "evaluations:auto_population_rule_toggle",
                title="Toggle enabled",
                icon_class="fa-solid fa-pause",
                hx_method="post",
            ),
            actions.AjaxAction(
                "evaluations:auto_population_rule_delete",
                title="Delete",
                icon_class="fa-solid fa-trash",
                hx_method="delete",
                confirm_message="Delete this rule?",
            ),
        ]
    )

    class Meta:
        model = DatasetAutoPopulationRule
        fields = ("source_experiment", "is_enabled", "last_run_at", "last_run_status", "last_error", "actions")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No auto-population rules configured."


def _row_class_factory(table, record):
    class_defaults = settings.DJANGO_TABLES2_ROW_ATTRS["class"]
    if (
        hasattr(table, "highlight_message_id")
        and table.highlight_message_id
        and record.id == table.highlight_message_id
    ):
        return f"{class_defaults} bg-yellow-100 dark:bg-yellow-900/20"
    return class_defaults


class DatasetMessagesTable(tables.Table):
    human_message_content = TemplateColumn(
        template_name="evaluations/dataset_message_human_column.html",
        verbose_name="Human Message",
        orderable=False,
    )
    ai_message_content = TemplateColumn(
        template_name="evaluations/dataset_message_ai_column.html",
        verbose_name="AI Message",
        orderable=False,
    )
    context = TemplateColumn(
        template_name="evaluations/dataset_message_dict_column.html",
        verbose_name="Context",
        orderable=False,
        extra_context={"field": "context"},
    )
    history = TemplateColumn(
        template_name="evaluations/dataset_message_history_column.html",
        verbose_name="History",
        orderable=False,
    )
    participant_data = TemplateColumn(
        template_name="evaluations/dataset_message_dict_column.html",
        verbose_name="Participant Data",
        orderable=False,
        extra_context={"field": "participant_data"},
    )
    session_state = TemplateColumn(
        template_name="evaluations/dataset_message_dict_column.html",
        verbose_name="Session State",
        orderable=False,
        extra_context={"field": "session_state"},
    )
    source = TemplateColumn(
        template_name="evaluations/dataset_message_source_column.html",
        verbose_name="Source",
        orderable=False,
    )
    link = TemplateColumn(
        template_name="evaluations/dataset_message_link_column.html",
        verbose_name="",
        orderable=False,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.Action(
                url_name="evaluations:edit_message_modal",
                template="evaluations/dataset_message_edit_action.html",
            ),
            actions.delete_action(
                url_name="evaluations:delete_message",
                url_factory=lambda url_name, request, record, value: reverse(
                    url_name, args=[request.team.slug, record.id]
                ),
                confirm_message="Are you sure you want to delete this message? This action cannot be undone.",
            ),
        ]
    )

    def __init__(self, *args, highlight_message_id=None, dataset_id=None, evaluation_mode=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_message_id = highlight_message_id
        self.dataset_id = dataset_id
        if evaluation_mode == EvaluationMode.SESSION:
            self.columns.hide("human_message_content")
            self.columns.hide("ai_message_content")

    class Meta:
        model = EvaluationMessage
        fields = (
            "link",
            "source",
            "human_message_content",
            "ai_message_content",
            "context",
            "history",
            "participant_data",
            "session_state",
            "actions",
        )
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "class": _row_class_factory,
        }
        orderable = False
        empty_text = "No messages in this dataset yet."
