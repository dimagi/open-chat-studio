from django.db.models import Q

from .services import BannerService


def banner_context(request):
    """
    Context processor that makes banners available in all templates.
    Uses the banner_location set by the middleware and excludes dismissed banners.
    """
    location = getattr(request, "banner_location", None)
    context = {"banners": []}
    location_filter = Q(location=location) | Q(location="global") if location else Q(location="global")
    banner_context = BannerService.get_banner_context(request, location_filter)
    context["banners"] = banner_context.get("banners", [])

    return context
