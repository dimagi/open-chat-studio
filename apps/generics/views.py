from django import views
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render


class BaseCombinedForm(views.View):
    """This view should be used as a base view for creating a new service config of
    a specific service type"""

    title = None
    extra_context = None
    model = None

    _object = None

    def get(self, request, team_slug: str, pk: int = None):
        return render(request, "generic/combined_object_form.html", self.get_context_data())

    def post(self, request, team_slug: str, pk: int = None):
        combined_form = self.get_combined_form(request.POST)
        if combined_form.is_valid():
            self.form_valid(combined_form)
            return HttpResponseRedirect(self.get_success_url())
        return render(request, "generic/combined_object_form.html", self.get_context_data())

    def form_valid(self, combined_form):
        instance = combined_form.save()
        instance.save()

    def get_success_url(self):
        raise NotImplementedError

    def get_context_data(self):
        extra_context = self.extra_context or {}
        form = self.get_combined_form()
        obj = self.get_object()
        return {
            "title": self.title,
            "combined_form": form,
            "secondary_key": form.get_secondary_key(obj),
            "button_text": "Update" if obj else "Create",
            **extra_context,
        }

    def get_combined_form(self, data=None):
        raise NotImplementedError

    def get_object(self):
        if self.kwargs.get("pk") and not self._object:
            self._object = get_object_or_404(self.model, team=self.request.team, pk=self.kwargs["pk"])
        return self._object

    def get_title(self):
        obj = self.get_object()
        if obj:
            return f"Edit {obj.name}"
        return self.title or f"Create {self.model.__name__}"
