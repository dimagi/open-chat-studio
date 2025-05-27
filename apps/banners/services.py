import json

from django.utils import timezone

from .models import Banner


class BannerService:
    @staticmethod
    def get_active_banners(request=None, location=None):
        now = timezone.now()
        query = Banner.objects.filter(is_active=True, start_date__lte=now, end_date__gt=now)

        if location:
            query = query.filter(location=location)
        if request:
            dismissed_ids = request.COOKIES.get("dismissed_banners", "[]")
            try:
                dismissed_list = json.loads(dismissed_ids)
                if dismissed_list:
                    query = query.exclude(id__in=dismissed_list)
            except (json.JSONDecodeError, ValueError):
                pass

        return query

    @staticmethod
    def get_banner_context(request=None, location=None):
        """
        Return context dictionary for banners.
        """
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
