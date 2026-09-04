from django.contrib import sitemaps
from django.urls import reverse

from .meta import get_protocol


class StaticViewSitemap(sitemaps.Sitemap):
    """
    Sitemap for serving any static content you want.
    """

    @property
    def protocol(self):
        return get_protocol()

    def items(self):
        # add any urls (by name) for static content you want to appear in your sitemap to this list
        #
        # Only the landing page. The marketing pages that used to be listed here are
        # 301s to the marketing site now, and a sitemap that nominates redirects tells
        # Google to keep crawling this host for content it should be reading there.
        return ["prelogin:home"]

    def location(self, item):  # ty: ignore[invalid-method-override]
        return reverse(item)
