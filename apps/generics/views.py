from django import views
from django.contrib import messages
from django.db.models import Prefetch
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.utils.translation import gettext

from apps.annotations.models import CustomTaggedItem, Tag
from apps.experiments.decorators import experiment_session_view
from apps.experiments.models import ExperimentSession
from apps.files.forms import get_file_formset
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
    template = "generic/type_select_form.html"

    _object = None

    def get(self, request, *args, **kwargs):
        form = self.get_form()
        return render(request, self.template, self.get_context_data(form))

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)

        file_formset = None
        if request.FILES:
            secondary_form_key = form.primary[form.secondary_key_field].value()
            secondary_form = form.secondary[secondary_form_key]
            file_formset = get_file_formset(request, formset_cls=secondary_form.file_formset_form)

        if form.is_valid() and (not file_formset or file_formset.is_valid()):
            self.form_valid(form, file_formset)
            return HttpResponseRedirect(self.get_success_url())

        if file_formset and not file_formset.is_valid():
            messages.error(request, ", ".join(file_formset.non_form_errors()))
        return render(request, "generic/type_select_form.html", self.get_context_data(form))

    def form_valid(self, form, file_formset):
        instance = form.save()
        instance.save()

    def get_context_data(self, form):
        extra_context = self.extra_context or {}
        obj = self.get_object()
        return {
            "title": self.title,
            "form": form,
            "secondary_key": form.get_secondary_key(obj),
            "object": obj,
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


def render_session_details(
    request, team_slug, experiment_id, session_id, active_tab, template_path, session_type="Experiment"
):
    session = ExperimentSession.objects.prefetch_related(
        Prefetch(
            "chat__tagged_items",
            queryset=CustomTaggedItem.objects.select_related("tag", "user"),
            to_attr="prefetched_tagged_items",
        )
    ).get(external_id=session_id, team__slug=team_slug)
    experiment = request.experiment
    participant = session.participant
    return TemplateResponse(
        request,
        template_path,
        {
            "experiment": experiment,
            "experiment_session": session,
            "active_tab": active_tab,
            "details": [
                (gettext("Participant"), session.get_participant_chip()),
                (gettext("Remote ID"), participant.remote_id if participant and participant.remote_id else "-"),
                (gettext("Status"), session.get_status_display),
                (gettext("Started"), session.consent_date or session.created_at),
                (gettext("Ended"), session.ended_at or "-"),
                (gettext(session_type), experiment.name),
            ],
            "available_tags": [t.name for t in Tag.objects.filter(team__slug=team_slug, is_system_tag=False).all()],
            "event_triggers": [
                {
                    "event_logs": trigger.event_logs.filter(session=session).order_by("-created_at").all(),
                    "trigger": trigger,
                }
                for trigger in experiment.event_triggers
            ],
            "participant_schedules": session.participant.get_schedules_for_experiment(
                experiment.id, as_dict=True, include_inactive=True
            ),
            "participant_id": session.participant_id,
        },
    )


@experiment_session_view()
def paginate_session(request, team_slug, experiment_id, session_id, view_name):
    session = request.experiment_session
    experiment = request.experiment
    query = ExperimentSession.objects.exclude(external_id=session_id).filter(experiment=experiment)
    if request.GET.get("dir", "next") == "next":
        next_session = query.filter(created_at__gte=session.created_at).order_by("created_at").first()
    else:
        next_session = query.filter(created_at__lte=session.created_at).order_by("created_at").last()
    if not next_session:
        messages.warning(request, "No more sessions to paginate")
        return redirect(view_name, team_slug, experiment_id, session_id)
    return redirect(view_name, team_slug, experiment_id, next_session.external_id)
