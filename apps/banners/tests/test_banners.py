from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.banners.models import Banner
from apps.banners.services import BannerService
from apps.teams.models import Flag
from apps.utils.factories.team import TeamFactory


class BannerServiceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.now = timezone.now()

        self.team_with_flag = TeamFactory(name="Team With Flag")
        self.team_without_flag = TeamFactory(name="Team Without Flag")

        self.test_flag = Flag.objects.create(name="flag_test_banner", everyone=False)
        self.test_flag.teams.add(self.team_with_flag)

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

        self.flagged_banner = Banner.objects.create(
            title="Flagged Banner",
            message="Feature flag message",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            feature_flag=self.test_flag,
        )

    def test_get_active_banners_no_location(self):
        result = BannerService.get_active_banners([], None, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.active_location_banner.id not in banner_ids
        assert self.inactive_banner.id not in banner_ids
        assert self.expired_banner.id not in banner_ids
        assert self.future_banner.id not in banner_ids

    def test_get_active_banners_with_location(self):
        result = BannerService.get_active_banners([], "Pipelines", None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.active_location_banner.id in banner_ids
        assert self.inactive_banner.id not in banner_ids
        assert self.expired_banner.id not in banner_ids
        assert self.future_banner.id not in banner_ids

    def test_get_active_banners_with_dismissed_ids(self):
        result = BannerService.get_active_banners([self.active_global_banner.id], None, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id not in banner_ids

    def test_get_active_banners_with_team_that_has_flag(self):
        """Test that banners with feature flags show for teams that have the flag enabled."""
        result = BannerService.get_active_banners([], "global", self.team_with_flag)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.flagged_banner.id in banner_ids

    def test_get_active_banners_with_team_without_flag(self):
        """Test that banners with feature flags don't show for teams without the flag."""
        result = BannerService.get_active_banners([], "global", self.team_without_flag)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.flagged_banner.id not in banner_ids
