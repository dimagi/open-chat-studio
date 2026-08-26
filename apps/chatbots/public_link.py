from django.http import HttpResponseNotFound

from apps.web.waf import WafRule, waf_allow


@waf_allow(WafRule.NoUserAgent_HEADER)
def public_link_page(request, token: str):
    return HttpResponseNotFound()
