from django.conf import settings
from django.urls import reverse
from django_tables2 import columns, tables

from apps.experiments.models import (
    ConsentForm,
    Experiment,
    ExperimentRoute,
    SourceMaterial,
    Survey,
)
from apps.generics import actions


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
