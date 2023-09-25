from django.contrib import admin
from waffle.admin import FlagAdmin as WaffleFlagAdmin

from .models import Flag, Invitation, Membership, Team


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "team", "role", "created_at"]
    list_filter = ["team"]


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ["id", "team", "email", "role", "is_accepted"]
    list_filter = ["team", "is_accepted"]


class MembershipInlineAdmin(admin.TabularInline):
    model = Membership
    list_display = ["user", "role"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    inlines = (MembershipInlineAdmin,)


MAX_TEAMS_DISPLAY = 3


@admin.display(description="Teams")
def teams_list(flag):
    """Return set of teams, for display in admin list. If there are more than
    MAX_TEAMS_DISPLAY, show that many followed by ellipsis."""
    if flag.teams.count() > MAX_TEAMS_DISPLAY:
        return list([team.name for team in flag.teams.all()][:MAX_TEAMS_DISPLAY] + ["..."])
    return [team.name for team in flag.teams.all()]


@admin.register(Flag)
class FlagAdmin(WaffleFlagAdmin):
    list_display = tuple(list(WaffleFlagAdmin.list_display) + [teams_list])
    list_filter = tuple(list(WaffleFlagAdmin.list_filter) + ["teams"])
    raw_id_fields = tuple(list(WaffleFlagAdmin.raw_id_fields) + ["teams"])
