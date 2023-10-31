from django import views
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render

from apps.generics.type_select_form import TypeSelectForm


class BaseTypeSelectFormView(views.View):
    """This view should be used as a base view for creating a new service config of
    a specific service type.

    Subclasses must provide the following fields:
    * title: Page title
    * model: Django model used to load the object for editing. Altertanively, override the get_object method.
    * get_form: Method that returns a TypeSelectForm instance. This method should be overridden.
    * get_success_url: Method that returns the URL to redirect to after a successful form submission. This method
        should be overridden.
    * extra_context (optional): Provided extra context to the view
    """

    title = None
    extra_context = None
    model = None

    _object = None

    def get(self, request, *args, **kwargs):
        form = self.get_form()
        return render(request, "generic/type_select_form.html", self.get_context_data(form))

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)
        if form.is_valid():
            self.form_valid(form)
            return HttpResponseRedirect(self.get_success_url())
        return render(request, "generic/type_select_form.html", self.get_context_data(form))

    def form_valid(self, form):
        instance = form.save()
        instance.save()

    def get_context_data(self, form):
        extra_context = self.extra_context or {}
        obj = self.get_object()
        return {
            "title": self.title,
            "form": form,
            "secondary_key": form.get_secondary_key(obj),
            "button_text": "Update" if obj else "Create",
            **extra_context,
        }

    def get_object(self):
        if self.kwargs.get("pk") and not self._object:
            self._object = get_object_or_404(self.model, team=self.request.team, pk=self.kwargs["pk"])
        return self._object

    def get_title(self):
        obj = self.get_object()
        if obj:
            return f"Edit {obj.name}"
        return self.title or f"Create {self.model.__name__}"

    def get_form(self, data=None) -> TypeSelectForm:
        raise NotImplementedError

    def get_success_url(self) -> str:
        raise NotImplementedError
