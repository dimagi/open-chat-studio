from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from apps.teams.decorators import login_and_team_required
from apps.teams.flags import get_all_flag_info
from apps.teams.models import Flag


class FeatureFlagForm(forms.Form):
    """Form for managing team feature flags."""

    def __init__(self, *args, **kwargs):
        self.team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)

        self._all_flags = None

        flag_info = get_all_flag_info()

        for flag_name, info in flag_info.items():
            if not info.teams_can_manage:
                continue

            label = flag_name.split("_", 1)[-1]
            help_text = f"Flag: {label}"
            if info.docs_slug:
                help_text += format_html(' (<a class="link" target="_blank" href="{}">docs</a>)', info.docs_url)
            self.fields[flag_name] = forms.BooleanField(
                label=info.description,
                required=False,
                help_text=mark_safe(help_text),
                initial=self._is_flag_active_for_team(flag_name),
            )

    def _is_flag_active_for_team(self, flag_name):
        """Check if a flag is active for the current team."""
        if not self.team:
            return False

        try:
            flag = Flag.objects.get(name=flag_name)
            return flag.is_active_for_team(self.team)
        except Flag.DoesNotExist:
            return False

    def save(self):
        """Save the form by updating team flag associations."""
        if not self.team:
            return

        flag_infos = get_all_flag_info()

        for flag_name, is_enabled in self.cleaned_data.items():
            flag_info = flag_infos.get(flag_name)
            if not flag_info or not flag_info.teams_can_manage:
                continue

            flag = self._get_or_create_flag(flag_name)
            if is_enabled:
                flag.teams.add(self.team)
                # Auto-enable required flags
                for required_flag_name in flag_info.requires:
                    required_flag = self._get_or_create_flag(required_flag_name)
                    required_flag.teams.add(self.team)
            else:
                flag.teams.remove(self.team)

            # Clear the cache to ensure the flag state is updated
            flag.flush()

    def _get_or_create_flag(self, flag_name):
        if not self._all_flags:
            self._all_flags = {flag.name: flag for flag in Flag.get_all()}

        if flag_name not in self._all_flags:
            flag, _created = Flag.objects.get_or_create(name=flag_name, defaults={"everyone": False})
            self._all_flags[flag_name] = flag
        return self._all_flags[flag_name]


@login_and_team_required
def feature_flags(request, team_slug):
    """Manage feature flags for the current team."""
    team = request.team
    is_team_admin = request.team_membership.is_team_admin

    if request.method == "POST" and is_team_admin:
        form = FeatureFlagForm(request.POST, team=team)
        if form.is_valid():
            form.save()
            messages.success(request, _("Feature flags updated successfully."))
            return redirect("single_team:feature_flags", team_slug=team.slug)
    else:
        form = FeatureFlagForm(team=team)

    return render(
        request,
        "teams/feature_flags.html",
        {
            "form": form,
            "team": team,
            "is_team_admin": is_team_admin,
        },
    )
