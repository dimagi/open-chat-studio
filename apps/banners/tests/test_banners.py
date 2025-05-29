import json
from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.banners.models import Banner
from apps.banners.services import BannerService


class BannerServiceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.now = timezone.now()

        self.active_global_banner = Banner.objects.create(
            title="Global Banner",
            message="Global message",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
        )

        self.active_location_banner = Banner.objects.create(
            title="Location Banner",
            message="Location message",
            banner_type="warning",
            location="Pipelines",
            is_active=True,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
        )

        self.inactive_banner = Banner.objects.create(
            title="Inactive Banner",
            message="Inactive message",
            banner_type="error",
            location="global",
            is_active=False,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
        )

        self.expired_banner = Banner.objects.create(
            title="Expired Banner",
            message="Expired message",
            banner_type="success",
            location="global",
            is_active=True,
            start_date=self.now - timedelta(days=2),
            end_date=self.now - timedelta(days=1),
        )

        self.future_banner = Banner.objects.create(
            title="Future Banner",
            message="Future message",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=self.now + timedelta(days=1),
            end_date=self.now + timedelta(days=2),
        )

    def test_get_active_banners_no_location(self):
        result = BannerService.get_active_banners("[]", None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.active_location_banner.id not in banner_ids
        assert self.inactive_banner.id not in banner_ids
        assert self.expired_banner.id not in banner_ids
        assert self.future_banner.id not in banner_ids

    def test_get_active_banners_with_location(self):
        result = BannerService.get_active_banners("[]", "Pipelines")
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.active_location_banner.id in banner_ids
        assert self.inactive_banner.id not in banner_ids
        assert self.expired_banner.id not in banner_ids
        assert self.future_banner.id not in banner_ids

    def test_get_active_banners_with_dismissed_ids(self):
        dismissed_ids = json.dumps([self.active_global_banner.id])
        result = BannerService.get_active_banners(dismissed_ids, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id not in banner_ids

    def test_get_active_banners_invalid_json(self):
        result = BannerService.get_active_banners("invalid json", None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids

    def test_get_active_banners_empty_string_dismissed_ids(self):
        result = BannerService.get_active_banners("", None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids

    def test_get_active_banners_invalid_list_items(self):
        dismissed_ids = json.dumps([1, "invalid", -1, 0])
        result = BannerService.get_active_banners(dismissed_ids, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
