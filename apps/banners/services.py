import json

from django.db.models import Q
from django.utils import timezone

from .models import Banner


class BannerService:
    @staticmethod
    def get_active_banners(dismissed_ids, location, team):
        now = timezone.now()
        location_filter = Q(location=location) | Q(location="global") if location else Q(location="global")
        query = Banner.objects.filter(is_active=True, start_date__lte=now, end_date__gt=now).filter(location_filter)
        try:
            dismissed_list = json.loads(dismissed_ids)
            if isinstance(dismissed_list, list) and all(isinstance(x, int) and x > 0 for x in dismissed_list):
                query = query.exclude(id__in=dismissed_list)
        except (json.JSONDecodeError, ValueError):
            pass
        if team:
            banners = list(query)
            visible_banners = [banner for banner in banners if banner.is_visible_for_team(team)]
            return visible_banners
        else:
            return query.filter(feature_flag__isnull=True)

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
