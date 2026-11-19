from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.http import urlencode
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.teams.backends import make_user_team_owner
from apps.teams.decorators import login_and_team_required
from apps.teams.deletion import TeamDeletionError, cancel_team_deletion, request_team_deletion
from apps.teams.forms import (
    InvitationForm,
    NotifyRecipientsForm,
    TeamChangeForm,
    TeamMfaForm,
    TeamMigrationForm,
    TeamPublicKeyForm,
)
from apps.teams.helpers import get_pending_team_access
from apps.teams.invitations import send_invitation
from apps.teams.models import Invitation, Membership, Team
from apps.teams.tasks import start_team_files_export
from apps.teams.utils import current_team
from apps.teams.views.members_views import ROLE_CHOICES
from apps.teams.views.team_settings import public_key_context, render_team_settings
from apps.web.elevation import Elevation, Grant


@login_required
def create_team(request):
    if request.method == "POST":
        form = TeamChangeForm(request.POST)
        if form.is_valid():
            team = form.save(commit=False)
            team.created_by = request.user
            team.save()
            with current_team(team):
                make_user_team_owner(team=team, user=request.user)
            return HttpResponseRedirect(reverse("single_team:manage_team", args=[team.slug]))
    else:
        form = TeamChangeForm()
    return render(
        request,
        "teams/manage_team.html",
        {
            "team_form": form,
            "create": True,
            "page_title": _("Create Team"),
        },
    )


@require_POST
@permission_required("teams.delete_team", raise_exception=True)
def delete_team(request, team_slug):
    form = NotifyRecipientsForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Choose who to notify about the deletion."))
        return HttpResponseRedirect(reverse("single_team:manage_team", args=[team_slug]))
    try:
        team = request_team_deletion(request.team, request.user, form.cleaned_data["notification_recipients"])
    except TeamDeletionError as e:
        messages.error(request, str(e))
        return HttpResponseRedirect(reverse("single_team:manage_team", args=[team_slug]))

    messages.success(
        request,
        _('The "{team}" team has been disabled and will be permanently deleted on {date}.').format(
            team=team.name, date=date_format(localtime(team.deletion_due_at), "DATETIME_FORMAT")
        ),
    )
    return HttpResponseRedirect(reverse("teams:pending_deletion", args=[team.slug]))


def _pending_team_access(request, slug) -> tuple[Team, bool]:
    """The pending team and whether the user may cancel its deletion; 404 for anyone who may not see it."""
    team = get_object_or_404(Team, slug=slug, deletion_requested_at__isnull=False)
    can_view, can_cancel = get_pending_team_access(request, team)
    if not can_view:
        raise Http404
    return team, can_cancel


@login_required
def team_pending_deletion(request, slug):
    """Shown in place of every page of a team that is scheduled for deletion."""
    if request.user.is_superuser and not Membership.objects.filter(team__slug=slug, user=request.user).exists():
        if not Elevation(request).has(Grant.team(slug)):
            elevate_url = reverse("web:elevate_team", args=[slug])
            return HttpResponseRedirect(f"{elevate_url}?{urlencode({'next': request.get_full_path()})}")
    team, can_cancel = _pending_team_access(request, slug)
    return render(
        request,
        "teams/pending_deletion.html",
        {"pending_team": team, "can_cancel": can_cancel, "page_title": _("Team pending deletion")},
    )


@require_POST
@login_required
def cancel_team_deletion_view(request, slug):
    team, can_cancel = _pending_team_access(request, slug)
    if not can_cancel:
        raise Http404
    try:
        with current_team(team):
            cancel_team_deletion(team)
    except TeamDeletionError as e:
        messages.error(request, str(e))
        return HttpResponseRedirect(reverse("prelogin:home"))
    messages.success(request, _('The "{team}" team has been restored.').format(team=team.name))
    return HttpResponseRedirect(team.dashboard_url)


@require_POST
@permission_required("teams.change_invitation", raise_exception=True)
def resend_invitation(request, team_slug, invitation_id):
    invitation = get_object_or_404(Invitation, team=request.team, id=invitation_id)
    send_invitation(invitation)
    return HttpResponse('<span class="btn btn-ghost is-disbled btn-disabled">Sent!</span>')


@require_POST
@permission_required("teams.add_invitation", raise_exception=True)
def send_invitation_view(request, team_slug):
    form = InvitationForm(request.team, request.POST)
    if form.is_valid():
        invitation = form.save(commit=False)
        invitation.team = request.team
        invitation.invited_by = request.user
        try:
            # we have to do validation again on the model because the team wasn't set when form validation happened
            invitation.validate_unique()
        except ValidationError as e:
            form.add_error(None, e.messages[0])
        else:
            invitation.save()
            form.save_m2m()
            send_invitation(invitation)
            form = InvitationForm(request.team)  # clear saved data from the form
    else:
        pass
    return render(
        request,
        "teams/components/members_section.html",
        {
            "invitation_form": form,
            "members_table_url": reverse("single_team:members_table", args=[request.team.slug]),
            "role_choices": ROLE_CHOICES,
        },
    )


@require_POST
@permission_required("teams.change_team", raise_exception=True)
def set_public_key(request, team_slug):
    """Saves the public key, the export scope and the migration-mode toggle together: they are set
    in one action on the Migration card, before a migration starts."""
    form = TeamPublicKeyForm(request.POST, instance=request.team)
    if form.is_valid():
        form.save()
        messages.success(request, _("Migration settings saved."))
        # A fresh form, so the card's starting allowlist is the one just saved.
        form = TeamPublicKeyForm(instance=request.team)
    else:
        messages.error(request, _("Could not save the migration settings."))
        # ModelForm.is_valid() has already written the submitted (rejected) values onto
        # request.team in memory via _post_clean(), even though nothing was saved. The
        # migration-mode checkbox and the card's badges read request.team directly, so
        # refresh the instance from the database to undo that in-memory mutation -- this
        # doesn't touch form.errors, which is what still surfaces the field errors.
        request.team.refresh_from_db()
    return render_team_settings(request, "data", **public_key_context(form))


def _set_team_boolean_field(
    request, form_class, field_name, section, *, enabled_message, disabled_message, error_message
):
    """Shared toggle-a-single-boolean-field flow for the team settings forms."""
    form = form_class(request.POST, instance=request.team)
    if form.is_valid():
        form.save()
        armed = form.cleaned_data[field_name]
        messages.success(request, enabled_message if armed else disabled_message)
    else:
        messages.error(request, error_message)
    return render_team_settings(request, section)


@require_POST
@permission_required("teams.change_team", raise_exception=True)
def set_migration_lock(request, team_slug):
    return _set_team_boolean_field(
        request,
        TeamMigrationForm,
        "is_migrating",
        "data",
        enabled_message=_("Migration mode enabled."),
        disabled_message=_("Migration mode disabled."),
        error_message=_("Could not update migration mode."),
    )


@require_POST
@permission_required("teams.change_team", raise_exception=True)
def set_require_mfa(request, team_slug):
    return _set_team_boolean_field(
        request,
        TeamMfaForm,
        "require_mfa",
        "members",
        enabled_message=_("MFA requirement enabled."),
        disabled_message=_("MFA requirement disabled."),
        error_message=_("Could not update the MFA requirement."),
    )


@require_POST
@permission_required("teams.delete_invitation", raise_exception=True)
def cancel_invitation_view(request, team_slug, invitation_id):
    invitation = get_object_or_404(Invitation, team=request.team, id=invitation_id)
    invitation.delete()
    return HttpResponse("")


@require_POST
@login_and_team_required
def download_team_files(request, team_slug):
    """Start a background task that zips up the team's files.

    If an export is already in flight for this team, resumes tracking it
    instead of starting a second one. Returns a progress partial that polls
    for completion and then links to the generated zip, which is served via a
    pre-signed URL (see FileView).
    """
    if not request.team_membership.is_team_admin():
        raise PermissionDenied
    team = request.team
    task_id = start_team_files_export(team)
    return render(
        request,
        "teams/partials/download_files_progress.html",
        {"task_id": task_id, "team": team},
    )
