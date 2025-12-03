import django_tables2 as tables
from django.conf import settings
from django.urls import reverse
from django_tables2 import columns

from apps.generics import actions
from apps.generics.tables import TimeAgoColumn
from apps.oauth.models import OAuth2Application


def _update_url_factory(url_name, request, record, value):
    """Factory for update URL."""
    return reverse("oauth2_provider:application_edit", args=[record.pk])


def _delete_url_factory(url_name, request, record, value):
    """Factory for delete URL."""
    return reverse("oauth2_provider:application_delete", args=[record.pk])


class OAuth2ApplicationTable(tables.Table):
    """Table for displaying OAuth2 applications."""

    client_id = columns.Column(verbose_name="Client ID", orderable=False)
    authorization_grant_type = columns.Column(
        verbose_name="Grant Type",
        orderable=False,
    )
    created = TimeAgoColumn(verbose_name="Created", orderable=True)

    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                "oauth2_provider:application_edit",
                url_factory=_update_url_factory,
            ),
            actions.delete_action(
                "oauth2_provider:application_delete",
                url_factory=_delete_url_factory,
                confirm_message="Are you sure you want to delete this application?",
            ),
        ]
    )

    class Meta:
        model = OAuth2Application
        fields = ("name", "client_id", "authorization_grant_type", "created")
        orderable = False
        empty_text = "You haven't registered any OAuth applications yet."
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
