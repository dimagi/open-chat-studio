from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.teams.backends import make_user_team_owner
from apps.teams.decorators import login_and_team_required
from apps.teams.forms import (
    InvitationForm,
    TeamChangeForm,
    TeamMfaForm,
    TeamMigrationForm,
    TeamPublicKeyForm,
)
from apps.teams.invitations import send_invitation
from apps.teams.models import Invitation
from apps.teams.tasks import delete_team_async, start_team_files_export
from apps.teams.utils import current_team
from apps.teams.views.members_views import ROLE_CHOICES
from apps.teams.views.team_settings import render_team_settings


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
    notify_recipients = request.POST.get("notification_recipients", "self")
    delete_team_async.delay(request.team.id, request.user.email, notify_recipients)

    notify_recipients_text = {"self": "you", "admins": "admins", "all": "all team members"}

    messages.success(
        request,
        _(
            'The "{team}" team deletion process has started. An email will be sent to {user} once it is complete.'
        ).format(team=request.team.name, user=notify_recipients_text[notify_recipients]),
    )
    return HttpResponseRedirect(reverse("prelogin:home"))


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
    """Saves the public key and the migration-mode toggle together, matching the mockup's
    single "Save key" action for the whole Migration public key card."""
    form = TeamPublicKeyForm(request.POST, instance=request.team)
    if form.is_valid():
        form.save()
        messages.success(request, _("Public key saved!"))
    else:
        messages.error(request, _("Could not save the public key."))
        # ModelForm.is_valid() has already written the submitted (rejected) values onto
        # request.team in memory via _post_clean(), even though nothing was saved. The
        # migration-mode checkbox below reads request.team.is_migrating directly, so
        # refresh the instance from the database to undo that in-memory mutation -- this
        # doesn't touch form.errors, which is what still surfaces the "public_key" field
        # error to the user.
        request.team.refresh_from_db()
    return render_team_settings(request, "data", public_key_form=form)


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
