from django.utils import timezone

from .models import Banner


class BannerService:
    @staticmethod
    def get_active_banners(location=None):
        now = timezone.now()
        query = Banner.objects.filter(is_active=True, start_date__lte=now, end_date__gt=now)
        if location:
            query = query.filter(location=location)

        return query

    @staticmethod
    def get_banner_context(location=None):
        """
        Return context dictionary for banners.
        """
        banners = BannerService.get_active_banners(location)
        context = {
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
        return context
