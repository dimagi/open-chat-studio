from django.template.response import TemplateResponse
from django.urls import reverse

from apps.generics.help import render_help_with_link


def generic_home(request, team_slug: str, title: str, table_url_name: str, new_url: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": title.lower(),
            "title": title,
            "title_help_content": render_help_with_link("", title.lower()),
            "new_object_url": reverse(new_url, args=[team_slug]),
            "table_url": reverse(table_url_name, args=[team_slug]),
            "enable_search": True,
            "toggle_archived": True,
        },
    )
