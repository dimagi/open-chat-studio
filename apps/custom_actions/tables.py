import django_tables2 as tables
from django.conf import settings
from django.utils.html import format_html

from apps.custom_actions.models import CustomAction
from apps.generics import actions


class CustomActionTable(tables.Table):
    name = tables.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    health_status = tables.Column(verbose_name="Status", orderable=False)
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                "custom_actions:edit",
                required_permissions=["custom_actions.change_customaction"],
            ),
            actions.delete_action(
                "custom_actions:delete",
                required_permissions=["custom_actions.delete_customaction"],
            ),
        ]
    )

    class Meta:
        model = CustomAction
        fields = ("name", "description", "server_url", "health_status")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No actions found."

    def render_health_status(self, value, record):
        """Render health status with DaisyUI badge component."""
        if not record.health_endpoint:
            return format_html('<span class="text-gray-500">-</span>')
        
        # Map status to DaisyUI badge classes
        status_classes = {
            "up": "badge badge-success",
            "down": "badge badge-error",
            "unknown": "badge badge-ghost",
        }
        
        badge_class = status_classes.get(value, "badge badge-ghost")
        
        # Add a "Check Now" button using HTMX
        check_button = (
            f'<button hx-post="/teams/{{{{ request.team.slug }}}}/custom_actions/{record.pk}/check-health/" '
            f'hx-swap="outerHTML" hx-target="closest td" '
            f'class="btn btn-xs btn-ghost ml-2" title="Check now">'
            f'<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">'
            f'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />'
            f'</svg>'
            f'</button>'
        )
        
        return format_html(
            '<span class="{}">{}</span>{}',
            badge_class,
            value.capitalize(),
            format_html(check_button) if record.health_endpoint else ""
        )
