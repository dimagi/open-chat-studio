import json

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.teams.models import Team

from .models import Banner


class BannerService:
    @staticmethod
    def get_active_banners(dismissed_ids: str, location: str | None, team: Team | None) -> QuerySet:
        now = timezone.now()
        query = Banner.objects.filter(is_active=True, start_date__lte=now, end_date__gt=now)
        location_filter = Q(location=location) | Q(location="global") if location else Q(location="global")
        if team:
            combined_filter = location_filter & (Q(feature_flag__isnull=True) | Q(feature_flag__teams=team))
            query = query.filter(combined_filter).distinct()
        else:
            combined_filter = location_filter & Q(feature_flag__isnull=True)
            query = query.filter(combined_filter)
        try:
            dismissed_list = json.loads(dismissed_ids)
            if isinstance(dismissed_list, list) and all(isinstance(x, int) and x > 0 for x in dismissed_list):
                query = query.exclude(id__in=dismissed_list)
        except (json.JSONDecodeError, ValueError):
            pass
        return query

    @staticmethod
    def get_banner_context(request, location):
        dismissed_ids = request.COOKIES.get("dismissed_banners", "[]")
        team = getattr(request, "team", None)
        banners = BannerService.get_active_banners(dismissed_ids, location, team)
        return {
            "banners": [
                {
                    "title": banner.title,
                    "message": banner.message,
                    "type": banner.banner_type,
                    "id": banner.id,
                    "end_date": banner.end_date,
                }
                for banner in banners
            ],
        }
