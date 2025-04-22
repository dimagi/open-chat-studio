from django.contrib.auth.mixins import PermissionRequiredMixin
from django.urls import reverse
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.evaluations.forms import EvaluationConfigForm
from apps.evaluations.models import EvaluationConfig
from apps.evaluations.tables import EvaluationConfigTable
from apps.teams.mixins import LoginAndTeamRequiredMixin


class EvaluationHome(LoginAndTeamRequiredMixin, TemplateView):  # , PermissionRequiredMixin
    # permission_required = "pipelines.view_pipeline"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "evaluations",
            "title": "Evaluations",
            "new_object_url": reverse("evaluations:new", args=[team_slug]),
            "table_url": reverse("evaluations:table", args=[team_slug]),
            # "title_help_content": render_help_with_link(
            #     "Pipelines allow you to create more complex bots by combining one or more steps together.", "pipelines"  # noqa
            # ),
        }


class CreateEvaluation(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    # permission_required = "pipelines.add_pipeline"
    template_name = "generic/object_form.html"
    model = EvaluationConfig
    form_class = EvaluationConfigForm
    extra_context = {
        "title": "Create Evaluation",
        "button_text": "Create",
        # "active_tab": "tags",
    }

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:evaluations_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class EvaluationTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "pipelines.view_pipeline"
    model = EvaluationConfig
    paginate_by = 25
    table_class = EvaluationConfigTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            EvaluationConfig.objects.filter(team=self.request.team)
            # .annotate(run_count=Count("runs"))
            # .order_by("name")
        )


class EditEvaluation(UpdateView):
    model = EvaluationConfig
    form_class = EvaluationConfigForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Evaluation",
        "button_text": "Update",
        "active_tab": "evaluations",
    }

    def get_queryset(self):
        return EvaluationConfig.objects.filter(team=self.request.team)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:home", args=[self.request.team.slug])
