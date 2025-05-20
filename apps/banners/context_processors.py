from .services import BannerService


def banner_context(request):
    """
    Context processor that makes banners available in all templates.
    Uses the banner_location set by the middleware.
    """
    location = getattr(request, "banner_location", None)
    context = {"banners": []}
    if location:
        location_banners = BannerService.get_banner_context(location)
        context["banners"].extend(location_banners.get("banners", []))
    global_banners = BannerService.get_banner_context(location="global")
    context["banners"].extend(global_banners.get("banners", []))

    return context
