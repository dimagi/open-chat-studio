import pytest
from waffle.utils import get_cache

from apps.teams.flags import Flags
from apps.teams.models import Flag


@pytest.fixture()
def team_with_flag(team_with_users):
    """Enable the notifications feature flag for a team."""
    flag = Flag.objects.create(name=Flags.NOTIFICATIONS.slug)
    flag.teams.add(team_with_users)
    # Flush the cache to ensure the team association is recognized
    cache = get_cache()
    for key in flag.get_flush_keys():
        cache.delete(key)
    return team_with_users
