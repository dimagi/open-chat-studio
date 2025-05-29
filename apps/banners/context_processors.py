from .services import BannerService


def banner_context(request):
    """
    Context processor that makes banners available in all templates.
    Uses the banner_location set by the middleware and excludes dismissed banners.
    """
    location = getattr(request, "banner_location", None)
    context = {"banners": []}
    banner_context = BannerService.get_banner_context(request, location)
    context["banners"] = banner_context.get("banners", [])

    return context
