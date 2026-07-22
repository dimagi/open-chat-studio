from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from apps.teams.decorators import login_and_team_required
from apps.teams.metadata import get_team_metadata_fields


def _build_field(field: dict, initial: str) -> forms.Field:
    field_type = field["type"]
    if field_type == "select":
        choices = [("", "---------")] + [(option, option) for option in field["options"]]
        return forms.ChoiceField(label=field["label"], required=False, initial=initial, choices=choices)
    if field_type == "email":
        return forms.EmailField(label=field["label"], required=False, initial=initial)
    return forms.CharField(label=field["label"], required=False, initial=initial)


class TeamMetadataForm(forms.Form):
    """Form for editing a team's internal (staff-only) metadata."""

    def __init__(self, *args, **kwargs):
        self.team = kwargs.pop("team")
        super().__init__(*args, **kwargs)

        metadata = self.team.metadata or {}
        for field in get_team_metadata_fields():
            key = field["key"]
            self.fields[key] = _build_field(field, initial=metadata.get(key, ""))

    def save(self):
        metadata = dict(self.team.metadata or {})
        metadata.update(self.cleaned_data)
        self.team.metadata = metadata
        self.team.save(update_fields=["metadata"])


@login_and_team_required
def internal_metadata(request, team_slug):
    """Staff-only page for viewing and editing a team's internal metadata."""
    if not request.user.is_staff:
        raise Http404

    team = request.team
    if request.method == "POST":
        form = TeamMetadataForm(request.POST, team=team)
        if form.is_valid():
            form.save()
            messages.success(request, _("Internal metadata updated successfully."))
            return redirect("single_team:manage_team", team_slug=team.slug)
    else:
        form = TeamMetadataForm(team=team)

    return render(
        request,
        "teams/internal_metadata.html",
        {
            "form": form,
            "team": team,
        },
    )
