from django import views
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template.response import TemplateResponse
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment, ExperimentSession
from apps.files.forms import get_file_formset
from apps.generics.chips import Chip
from apps.generics.help import render_help_with_link
from apps.generics.type_select_form import TypeSelectForm
from apps.teams.decorators import login_and_team_required
from apps.experiments.views.experiment import get_events_context, get_routes_context, get_terminal_bots_context


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


HELP_TEXT_KEYS = {
    "Experiments": "experiment",
    "Chatbots": "chatbots",
}


def generic_home(request, team_slug: str, title: str, table_url_name: str, new_url: str):
    help_key = HELP_TEXT_KEYS.get(title, title.lower())  # Default to lowercase if missing
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": title.lower(),
            "title": title,
            "title_help_content": render_help_with_link("", help_key),
            "new_object_url": reverse(new_url, args=[team_slug]),
            "table_url": reverse(table_url_name, args=[team_slug]),
            "enable_search": True,
            "toggle_archived": True,
        },
    )


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def base_single_experiment_view(
    request, team_slug: str, experiment_id: int, template_name: str, active_tab: str, include_bot_type_chip=False
):
    experiment = get_object_or_404(Experiment.objects.get_all(), id=experiment_id, team=request.team)

    user_sessions = (
        ExperimentSession.objects.with_last_message_created_at()
        .filter(
            participant__user=request.user,
            experiment=experiment,
        )
        .exclude(experiment_channel__platform=ChannelPlatform.API)
    )

    channels = experiment.experimentchannel_set.exclude(platform__in=[ChannelPlatform.WEB, ChannelPlatform.API]).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)

    platform_forms = {}
    form_kwargs = {"experiment": experiment}
    for platform in available_platforms:
        if platform.form(**form_kwargs):
            platform_forms[platform] = platform.form(**form_kwargs)

    deployed_version = None
    if experiment != experiment.default_version:
        deployed_version = experiment.default_version.version_number

    bot_type_chip = None
    if include_bot_type_chip:
        if pipeline := experiment.pipeline:
            bot_type_chip = Chip(label=f"Pipeline: {pipeline.name}", url=pipeline.get_absolute_url())
        elif assistant := experiment.assistant:
            bot_type_chip = Chip(label=f"Assistant: {assistant.name}", url=assistant.get_absolute_url())

    context = {
        "active_tab": active_tab,
        "bot_type_chip": bot_type_chip,
        "experiment": experiment,
        "user_sessions": user_sessions,
        "platforms": available_platforms,
        "platform_forms": platform_forms,
        "channels": channels,
        "available_tags": [tag.name for tag in experiment.team.tag_set.filter(is_system_tag=False)],
        "deployed_version": deployed_version,
        "experiment_versions": experiment.get_version_name_list(),  # Added in both views
        **get_events_context(experiment, team_slug),
        **get_routes_context(experiment, team_slug),
        **get_terminal_bots_context(experiment, team_slug),
    }

    return TemplateResponse(request, template_name, context)
