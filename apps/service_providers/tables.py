from django.conf import settings
from django.urls import reverse
from django_tables2 import tables

from apps.generics import actions


def make_table(provider_type, model, fields=("type", "name")):
    meta_attrs = {
        "model": model,
        "fields": fields,
        "row_attrs": settings.DJANGO_TABLES2_ROW_ATTRS,
        "orderable": False,
    }
    Meta = type("Meta", (object,), meta_attrs)

    class_name = model.__name__ + "AutogeneratedTable"
    table_class_attrs = {
        "actions": actions.ActionsColumn(
            actions=[
                actions.edit_action(url_name="service_providers:edit", url_factory=_make_url_factory(provider_type)),
                actions.delete_action(
                    url_name="service_providers:delete",
                    url_factory=_make_url_factory(provider_type),
                    confirm_message="Continuing with this action will remove this tag from any tagged entity",
                ),
            ]
        ),
        "Meta": Meta,
    }
    return type(tables.Table)(class_name, (tables.Table,), table_class_attrs)


def _make_url_factory(provider_type):
    def url_factory(url_name, request, record, value):
        return reverse(url_name, args=[request.team.slug, provider_type, record.pk])

    return url_factory
