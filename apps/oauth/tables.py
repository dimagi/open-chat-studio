import django_tables2 as tables
from django.conf import settings
from django.urls import reverse
from django_tables2 import columns

from apps.generics import actions
from apps.generics.tables import TimeAgoColumn
from apps.oauth.models import OAuth2Application


def _pk_only_url_factory(url_name, request, record, value):
    """URL factory for actions on views that are not team-scoped."""
    return reverse(url_name, args=[record.pk])


class OAuth2ApplicationTable(tables.Table):
    """Table for displaying a team's OAuth2 applications."""

    client_id = columns.Column(verbose_name="Client ID", orderable=False)
    authorization_grant_type = columns.Column(
        verbose_name="Grant Type",
        orderable=False,
    )
    created = TimeAgoColumn(verbose_name="Created", orderable=True)

    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                "oauth_apps:edit",
                required_permissions=["oauth.change_oauth2application"],
            ),
            actions.delete_action(
                "oauth_apps:delete",
                required_permissions=["oauth.delete_oauth2application"],
                confirm_message="Are you sure you want to delete this application?",
            ),
        ]
    )

    class Meta:
        model = OAuth2Application
        fields = ("name", "client_id", "authorization_grant_type", "created")
        orderable = False
        empty_text = "This team hasn't registered any OAuth applications yet."
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS


class GlobalOAuth2ApplicationTable(OAuth2ApplicationTable):
    """Table for displaying global (team-less) OAuth2 applications."""

    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                "oauth2_provider:global_application_edit",
                url_factory=_pk_only_url_factory,
            ),
            actions.delete_action(
                "oauth2_provider:global_application_delete",
                url_factory=_pk_only_url_factory,
                confirm_message="Are you sure you want to delete this application?",
            ),
        ]
    )

    class Meta(OAuth2ApplicationTable.Meta):
        empty_text = "No global OAuth applications have been registered."
