from apps.admin.models import get_site_config


def ocs_config(request):
    """
    Context processor that makes banners available in all templates.
    Uses the banner_location set by the middleware and excludes dismissed banners.
    """
    return {"ocs_config": get_site_config()}
