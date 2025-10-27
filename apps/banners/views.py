from django.shortcuts import render

from .services import BannerService


def load_banners(request):
    """
    View to load banners dynamically via HTMX.
    Uses the banner_location set by the middleware.
    """
    location = getattr(request, "banner_location", None)
    banner_context = BannerService.get_banner_context(request, location)
    return render(request, "banners/banner_content.html", banner_context)
