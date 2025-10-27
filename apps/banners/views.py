from urllib.parse import urlparse

from django.shortcuts import render
from django.urls import resolve

from .services import BannerService

VIEW_NAME_MAPPING = {
    "pipelines:home": "pipelines",
    "pipelines:new": "pipelines_new",
    "chatbots:chatbots_home": "chatbots_home",
    "chatbots:new": "chatbots_new",
    "assistants:home": "assistants_home",
    "team:manage_team": "team_settings",
}


def load_banners(request):
    """
    View to load banners dynamically via HTMX.
    Determines the location from the HX-Current-URL header.
    """
    location = None

    # Get the current URL from HTMX header
    current_url = request.headers.get("HX-Current-URL")
    if current_url:
        try:
            # Parse the URL path
            parsed_url = urlparse(current_url)
            # Resolve the URL to a view name
            resolved = resolve(parsed_url.path)
            view_name = resolved.view_name
            # Map to banner location
            location = VIEW_NAME_MAPPING.get(view_name)
        except Exception:
            # If we can't resolve, default to None (global banners only)
            pass

    banner_context = BannerService.get_banner_context(request, location)
    return render(request, "banners/banner_content.html", banner_context)
