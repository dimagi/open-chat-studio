from django.conf import settings
from django_tables2 import columns, tables

from apps.experiments.models import (
    ConsentForm,
    Experiment,
    NoActivityMessageConfig,
    Prompt,
    SafetyLayer,
    SourceMaterial,
    Survey,
)
from apps.generics import table_actions


class ExperimentTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    description = columns.Column(verbose_name="Description")
    owner = columns.Column(accessor="owner.username", verbose_name="Created By")
    bot = columns.Column(accessor="chatbot_prompt__name", verbose_name="Bot", orderable=True)
    topic = columns.Column(accessor="source_material__topic", verbose_name="Topic", orderable=True)
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
    )

    class Meta:
        model = Experiment
        fields = ("name",)
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No experiments found."


class SafetyLayerTable(tables.Table):
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                table_actions.edit_action(url_name="experiments:safety_edit"),
                table_actions.delete_action(url_name="experiments:safety_delete"),
            ]
        },
    )

    class Meta:
        model = SafetyLayer
        fields = (
            "prompt",
            "messages_to_review",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No safety layers found."


class SourceMaterialTable(tables.Table):
    owner = columns.Column(accessor="owner.username", verbose_name="Created By")
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                table_actions.edit_action(url_name="experiments:source_material_edit"),
                table_actions.delete_action(url_name="experiments:source_material_delete"),
            ]
        },
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
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                table_actions.edit_action(url_name="experiments:survey_edit"),
                table_actions.delete_action(url_name="experiments:survey_delete"),
            ]
        },
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
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                table_actions.edit_action(url_name="experiments:consent_edit"),
                table_actions.delete_action(
                    url_name="experiments:consent_delete",
                    display_condition=lambda request, record: not record.is_default,
                ),
            ]
        },
    )

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


class NoActivityMessageConfigTable(tables.Table):
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                table_actions.edit_action(url_name="experiments:no_activity_edit"),
                table_actions.delete_action(url_name="experiments:no_activity_delete"),
            ]
        },
    )

    class Meta:
        model = NoActivityMessageConfig
        fields = (
            "name",
            "message_for_bot",
            "max_pings",
            "ping_after",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No configs found."


class PromptTable(tables.Table):
    owner = columns.Column(accessor="owner.get_display_name", verbose_name="Created By")
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                table_actions.edit_action(url_name="experiments:prompt_edit"),
                table_actions.delete_action(url_name="experiments:prompt_delete"),
            ]
        },
    )

    def render_description(self, value):
        if len(value) > 100:
            return f"{value[:100]}..."
        return value

    def render_prompt(self, value):
        if len(value) > 100:
            return f"{value[:100]}..."
        return value

    class Meta:
        model = Prompt
        fields = (
            "name",
            "description",
            "owner",
            "prompt",
            "input_formatter",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No prompts found."
