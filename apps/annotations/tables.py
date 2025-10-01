from django.conf import settings
from django.http import HttpRequest
from django.template import Context
from django_tables2 import columns, tables

from apps.generics import actions

from .models import Tag


def edit_display_condition(request: HttpRequest, record: Context) -> bool:
    return not record.is_system_tag and request.user.has_perm("annotations.change_tag")


def delete_display_condition(request: HttpRequest, record: Context) -> bool:
    return not record.is_system_tag and request.user.has_perm("annotations.delete_tag")


class TagTable(tables.Table):
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="annotations:tag_edit", display_condition=edit_display_condition),
            actions.delete_action(
                url_name="annotations:tag_delete",
                confirm_message="Continuing with this action will remove this tag from any tagged entity",
                display_condition=delete_display_condition,
            ),
        ]
    )
    is_system_tag = columns.BooleanColumn(yesno="✓,", verbose_name="Is System Tag")

    class Meta:
        model = Tag
        fields = ("name", "is_system_tag")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No tags found."
