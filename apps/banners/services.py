import re

from django.contrib.sites.models import Site
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.teams.models import Team

from .models import Banner

COOKIE_RX = re.compile(r"^dismissed_banner_(\d+)$")


class BannerService:
    @staticmethod
    def get_active_banners(
        dismissed_ids: list[int], location: str | None, team: Team | None, site: Site | None
    ) -> QuerySet:
        now = timezone.now()
        query = (
            Banner.objects.filter(is_active=True, start_date__lte=now, end_date__gt=now)
            .filter(Q(location=location) | Q(location="global") if location else Q(location="global"))
            .filter(Q(site=None) | Q(site=site) if site else Q(site=None))
        )
        if team:
            query = query.filter(Q(feature_flag__isnull=True) | Q(feature_flag__teams=team)).distinct()
        else:
            query = query.filter(Q(feature_flag__isnull=True))
        if dismissed_ids:
            query = query.exclude(id__in=dismissed_ids)
        return query

    @staticmethod
    def get_banner_context(request, location):
        dismissed_ids = get_dismissed_banner_ids(request)
        team = getattr(request, "team", None)
        try:
            site = Site.objects._get_site_by_request(request)
        except Site.DoesNotExist:
            site = None
        banners = []
        for banner in BannerService.get_active_banners(dismissed_ids, location, team, site):
            message = banner.get_formatted_message(request)
            if message:
                banners.append(
                    {
                        "title": banner.title,
                        "message": message,
                        "type": banner.banner_type,
                        "id": banner.id,
                        "end_date": banner.end_date,
                        "cookie_expires": banner.cookie_expires,
                    }
                )
        return {"banners": banners}


def get_dismissed_banner_ids(request):
    dismissed_ids = []
    for cookie in request.COOKIES:
        if match := COOKIE_RX.match(cookie):
            try:
                banner_id = int(match.group(1))
            except ValueError:
                pass
            else:
                dismissed_ids.append(banner_id)
    return dismissed_ids
