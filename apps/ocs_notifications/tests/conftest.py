from django.core.cache import cache

from apps.teams.models import Flag


def activate_flag_for_team(flag_name, team):
    """Activate a Waffle flag for a team and clear its cached state."""
    flag, _ = Flag.objects.get_or_create(name=flag_name)
    flag.teams.add(team)
    for key in flag.get_flush_keys():
        cache.delete(key)
    return flag
