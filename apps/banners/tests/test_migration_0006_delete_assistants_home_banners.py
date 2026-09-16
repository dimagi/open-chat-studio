import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.utils import timezone

from apps.banners.models import Banner

_migration = importlib.import_module("apps.banners.migrations.0006_alter_banner_location")
delete_assistants_home_banners = _migration.delete_assistants_home_banners


def _banner(location):
    return Banner.objects.create(
        message="hello",
        location=location,
        end_date=timezone.now() + timedelta(days=1),
    )


@pytest.mark.django_db()
def test_deletes_banners_pinned_to_the_removed_assistants_page():
    stale = _banner("assistants_home")

    delete_assistants_home_banners(apps, None)

    assert not Banner.objects.filter(pk=stale.pk).exists()


@pytest.mark.django_db()
def test_leaves_banners_on_surviving_locations_alone():
    kept = [_banner(location) for location, _ in Banner.LOCATIONS]

    delete_assistants_home_banners(apps, None)

    assert Banner.objects.filter(pk__in=[banner.pk for banner in kept]).count() == len(kept)
