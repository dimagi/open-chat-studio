from .models import Team


def migrating_team_ids():
    return Team.objects.filter(is_migrating=True).values_list("id", flat=True)
