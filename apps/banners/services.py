import json

from django.db.models import Q
from django.utils import timezone

from .models import Banner


class BannerService:
    @staticmethod
    def get_active_banners(request, location):
        now = timezone.now()
        location_filter = Q(location=location) | Q(location="global") if location else Q(location="global")
        query = Banner.objects.filter(is_active=True, start_date__lte=now, end_date__gt=now).filter(location_filter)

        dismissed_ids = request.COOKIES.get("dismissed_banners", "[]")
        try:
            dismissed_list = json.loads(dismissed_ids)
            if isinstance(dismissed_list, list) and all(isinstance(x, int) and x > 0 for x in dismissed_list):
                query = query.exclude(id__in=dismissed_list)
        except (json.JSONDecodeError, ValueError):
            pass

        return query

    @staticmethod
    def get_banner_context(request, location):
        banners = BannerService.get_active_banners(request, location)
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
