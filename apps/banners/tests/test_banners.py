from datetime import timedelta

from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.banners.models import Banner
from apps.banners.services import BannerService
from apps.teams.models import Flag
from apps.utils.factories.team import TeamFactory


class BannerServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.factory = RequestFactory()
        now = timezone.now()

        cls.team_with_flag = TeamFactory(name="Team With Flag")
        cls.team_without_flag = TeamFactory(name="Team Without Flag")

        cls.test_flag = Flag.objects.create(name="flag_test_banner", everyone=False)
        cls.test_flag.teams.add(cls.team_with_flag)

        cls.active_global_banner = Banner.objects.create(
            title="Global Banner",
            message="Global message",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
        )

        cls.active_location_banner = Banner.objects.create(
            title="Location Banner",
            message="Location message",
            banner_type="warning",
            location="Pipelines",
            is_active=True,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
        )

        cls.inactive_banner = Banner.objects.create(
            title="Inactive Banner",
            message="Inactive message",
            banner_type="error",
            location="global",
            is_active=False,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
        )

        cls.expired_banner = Banner.objects.create(
            title="Expired Banner",
            message="Expired message",
            banner_type="success",
            location="global",
            is_active=True,
            start_date=now - timedelta(days=2),
            end_date=now - timedelta(days=1),
        )

        cls.future_banner = Banner.objects.create(
            title="Future Banner",
            message="Future message",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=now + timedelta(days=1),
            end_date=now + timedelta(days=2),
        )

        cls.flagged_banner = Banner.objects.create(
            title="Flagged Banner",
            message="Feature flag message",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            feature_flag=cls.test_flag,
        )

        cls.site2 = Site.objects.create(id=2, domain="example.com", name="example")
        cls.site1_banner = Banner.objects.create(
            title="Site Banner",
            message="Site banner",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            site=Site.objects.get_current(),
        )
        cls.site2_banner = Banner.objects.create(
            title="Site Banner",
            message="Site banner",
            banner_type="info",
            location="global",
            is_active=True,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            site=cls.site2,
        )

    def test_get_active_banners_no_location(self):
        result = BannerService.get_active_banners([], None, None, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.active_location_banner.id not in banner_ids
        assert self.inactive_banner.id not in banner_ids
        assert self.expired_banner.id not in banner_ids
        assert self.future_banner.id not in banner_ids

    def test_get_active_banners_with_location(self):
        result = BannerService.get_active_banners([], "Pipelines", None, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.active_location_banner.id in banner_ids
        assert self.inactive_banner.id not in banner_ids
        assert self.expired_banner.id not in banner_ids
        assert self.future_banner.id not in banner_ids

    def test_get_active_banners_with_dismissed_ids(self):
        result = BannerService.get_active_banners([self.active_global_banner.id], None, None, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id not in banner_ids

    def test_get_active_banners_with_team_that_has_flag(self):
        """Test that banners with feature flags show for teams that have the flag enabled."""
        result = BannerService.get_active_banners([], "global", self.team_with_flag, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.flagged_banner.id in banner_ids

    def test_get_active_banners_with_team_without_flag(self):
        """Test that banners with feature flags don't show for teams without the flag."""
        result = BannerService.get_active_banners([], "global", self.team_without_flag, None)
        banner_ids = [banner.id for banner in result]

        assert self.active_global_banner.id in banner_ids
        assert self.flagged_banner.id not in banner_ids

    def test_get_active_banners_with_no_site(self):
        result = BannerService.get_active_banners([], "global", None, None)
        banner_ids = [banner.id for banner in result]
        assert self.active_global_banner.id in banner_ids
        assert self.site1_banner.id not in banner_ids
        assert self.site2_banner.id not in banner_ids

    def test_get_active_banners_with_site(self):
        result = BannerService.get_active_banners([], "global", None, self.site2)
        banner_ids = [banner.id for banner in result]
        assert self.active_global_banner.id in banner_ids
        assert self.site1_banner.id not in banner_ids
        assert self.site2_banner.id in banner_ids
