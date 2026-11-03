"""Staff-only page that renders every shared UI component and daisyUI pattern for design review."""

from django import forms
from django.template.response import TemplateResponse
from django.utils.translation import gettext

from apps.admin.breadcrumbs import admin_crumb
from apps.web.elevation import Grant, requires_elevation


class UiGalleryForm(forms.Form):
    """Sample fields, rendered through `render_form_fields` like every other form."""

    name = forms.CharField(label="Name", help_text="Shown to participants.")
    kind = forms.ChoiceField(label="Kind", choices=[("a", "Option A"), ("b", "Option B")])
    notes = forms.CharField(label="Notes", widget=forms.Textarea(attrs={"rows": 2}), required=False)
    enabled = forms.BooleanField(label="Enabled", required=False)


@requires_elevation(Grant.OCS_ADMIN)
def ui_gallery(request):
    form = UiGalleryForm(data={"name": "Support bot", "kind": "a"})
    form.is_valid()
    form.add_error("notes", gettext("Example of a field error."))
    return TemplateResponse(
        request,
        "admin/ui_gallery.html",
        context={
            "active_tab": "admin",
            "breadcrumbs": [admin_crumb(), (gettext("UI Gallery"), None)],
            "form": form,
            "gallery_breadcrumbs": [
                (gettext("Chatbots"), "#"),
                (gettext("Support bot"), "#"),
                (gettext("Sessions"), None),
            ],
            "gallery_changes": [
                {"type": "add", "path": "name", "value": "Alex"},
                {"type": "change", "path": "plan", "old": "free", "new": "pro"},
                {"type": "remove", "path": "legacy_id", "value": "42"},
            ],
        },
    )
