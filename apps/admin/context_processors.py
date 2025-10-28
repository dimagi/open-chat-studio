from apps.admin.models import get_site_config


def ocs_config(request):
    """
    Context processor that makes site config available
    """
    return {"ocs_config": get_site_config()}
