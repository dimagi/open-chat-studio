"""The team settings page: a sidebar of sections, one of which is rendered at a time.

Each section is declared once in ``SETTINGS_SECTIONS``, so its visibility rule is shared
between the nav that links to it and the view that serves it: a section a user cannot see
in the nav 404s when they request it directly.

Clicking a nav item issues an htmx GET against the section's own URL, which swaps the
sidebar and the section body together. The same URL served without the htmx header
renders the whole page, so deep links, refreshes and back/forward all work.
"""

from collections.abc import Callable
from dataclasses import dataclass

from celery.result import AsyncResult
from celery_progress.backend import PROGRESS_STATE
from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from waffle import flag_is_active

from apps.teams.decorators import login_and_team_required
from apps.teams.flags import Flags
from apps.teams.forms import (
    FeatureFlagForm,
    InvitationForm,
    NotifyRecipientsForm,
    TeamChangeForm,
    TeamMetadataForm,
    TeamPublicKeyForm,
)
from apps.teams.models import Invitation
from apps.teams.views.integrations_views import get_integration_new_choices, get_integration_rows
from apps.teams.views.members_views import ROLE_CHOICES
from apps.web.forms import set_form_fields_disabled

_ACTIVE_EXPORT_STATES = {"PENDING", "STARTED", PROGRESS_STATE}

DEFAULT_SECTION = "integrations"


def _team_files_export_context(team):
    """Resume progress for an in-flight export, or surface the last completed one.

    An in-flight task_id takes priority: it may be replacing an older
    `files_export` file, and the progress UI is mutually exclusive with the
    ready-to-download state.
    """
    task_id = team.files_export_task_id
    if task_id:
        if AsyncResult(task_id).state in _ACTIVE_EXPORT_STATES:
            return {"files_export_task_id": task_id}
        team.mark_files_export_finished()
    if team.files_export_id and team.files_export.file:
        return {"files_export_file": team.files_export}
    return {}


def _always_visible(request) -> bool:
    return True


def _no_context(request) -> dict:
    return {}


@dataclass(frozen=True)
class SettingsSection:
    key: str
    label: str
    icon: str
    template: str
    #: Key into the page's `stats` dict whose value is shown as a badge next to the nav item.
    count_key: str | None = None
    is_visible: Callable[..., bool] = _always_visible
    get_context: Callable[..., dict] = _no_context


def _is_team_admin(request) -> bool:
    return request.team_membership.is_team_admin()


def _can_view_notifications(request) -> bool:
    return bool(flag_is_active(request, Flags.SLACK_NOTIFICATIONS.slug)) and request.user.has_perm(
        "ocs_notifications.view_notificationchannel"
    )


def _is_staff(request) -> bool:
    return request.user.is_staff


def _integrations_context(request) -> dict:
    return {
        "integration_new_choices": get_integration_new_choices(request, request.team),
        "integrations_table_url": reverse("single_team:integrations_table", args=[request.team.slug]),
    }


def _members_context(request) -> dict:
    return {
        "invitation_form": InvitationForm(team=request.team),
        "members_table_url": reverse("single_team:members_table", args=[request.team.slug]),
        "role_choices": ROLE_CHOICES,
    }


def _data_context(request) -> dict:
    return {
        "public_key_form": TeamPublicKeyForm(instance=request.team),
        "notify_recipients_form": NotifyRecipientsForm(),
        **_team_files_export_context(request.team),
    }


def _flags_context(request) -> dict:
    return {
        "flags_form": FeatureFlagForm(team=request.team),
        "is_team_admin": _is_team_admin(request),
    }


def _internal_metadata_context(request) -> dict:
    return {"metadata_form": TeamMetadataForm(team=request.team)}


SETTINGS_SECTIONS = [
    SettingsSection(
        key="integrations",
        label=_("Integrations"),
        icon="fa-plug",
        template="teams/sections/integrations.html",
        count_key="integrations",
        get_context=_integrations_context,
    ),
    SettingsSection(
        key="notifications",
        label=_("Notifications"),
        icon="fa-bell",
        template="teams/sections/notifications.html",
        is_visible=_can_view_notifications,
    ),
    SettingsSection(
        key="members",
        label=_("Members"),
        icon="fa-users",
        template="teams/sections/members.html",
        count_key="members",
        get_context=_members_context,
    ),
    SettingsSection(
        key="developer",
        label=_("Developer"),
        icon="fa-code",
        template="teams/sections/developer.html",
    ),
    SettingsSection(
        key="data",
        label=_("Data"),
        icon="fa-box-archive",
        template="teams/sections/data.html",
        is_visible=_is_team_admin,
        get_context=_data_context,
    ),
    SettingsSection(
        key="flags",
        label=_("Feature Flags"),
        icon="fa-flag",
        template="teams/sections/feature_flags.html",
        get_context=_flags_context,
    ),
    SettingsSection(
        key="internal-metadata",
        label=_("Internal Metadata"),
        icon="fa-database",
        template="teams/sections/internal_metadata.html",
        is_visible=_is_staff,
        get_context=_internal_metadata_context,
    ),
]

_SECTIONS_BY_KEY = {section.key: section for section in SETTINGS_SECTIONS}


def section_url(team_slug: str, section_key: str | None = None) -> str:
    return reverse("single_team:manage_team_section", args=[team_slug, section_key or DEFAULT_SECTION])


def get_section(request, section_key: str | None) -> SettingsSection:
    section = _SECTIONS_BY_KEY.get(section_key or DEFAULT_SECTION)
    if section is None or not section.is_visible(request):
        raise Http404(f"No team settings section named '{section_key}'")
    return section


def _nav_items(request, active: SettingsSection, stats: dict) -> list[dict]:
    return [
        {
            "key": section.key,
            "label": section.label,
            "icon": section.icon,
            "url": section_url(request.team.slug, section.key),
            "count": stats.get(section.count_key) if section.count_key else None,
            "is_active": section.key == active.key,
        }
        for section in SETTINGS_SECTIONS
        if section.is_visible(request)
    ]


def _settings_context(request, section: SettingsSection, team_form=None) -> dict:
    team = request.team
    if team_form is None:
        team_form = TeamChangeForm(instance=team)
        if not _is_team_admin(request):
            set_form_fields_disabled(team_form, True)
    stats = {
        "members": team.membership_set.count(),
        "integrations": len(get_integration_rows(request, team)),
        "pending_invites": Invitation.objects.filter(team=team, is_accepted=False).count(),
    }
    return {
        "team": team,
        "active_tab": "manage-team",
        "page_title": _("My Team | {team}").format(team=team),
        "team_form": team_form,
        "active_section": section,
        "nav_sections": _nav_items(request, section, stats),
        "stats": stats,
        **section.get_context(request),
    }


def render_team_settings(request, section_key: str | None = None, *, team_form=None, **extra_context):
    """Render one settings section: the body alone over htmx, the whole page otherwise."""
    section = get_section(request, section_key)
    context = _settings_context(request, section, team_form=team_form) | extra_context
    template = "teams/partials/settings_body.html" if request.htmx else "teams/manage_team.html"
    return render(request, template, context)


@login_and_team_required
def manage_team(request, team_slug, section=None):
    team_form = None
    if request.method == "POST":
        if request.team_membership.is_team_admin():
            team_form = TeamChangeForm(request.POST, instance=request.team)
            if team_form.is_valid():
                messages.success(request, _("Team details saved!"))
                team_form.save()
                if request.team.slug != team_slug:
                    return HttpResponseRedirect(section_url(request.team.slug, section))
        else:
            messages.error(request, "Sorry you don't have permission to do that.")
    return render_team_settings(request, section, team_form=team_form)
