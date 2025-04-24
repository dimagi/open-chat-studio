from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView, columns, tables

from apps.evaluations.forms import EvaluationConfigForm
from apps.evaluations.models import EvaluationConfig, EvaluationRun
from apps.evaluations.tables import EvaluationConfigTable, EvaluationRunTable
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
        return reverse("evaluations:home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


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


class EvaluationRunHome(LoginAndTeamRequiredMixin, TemplateView):  # , PermissionRequiredMixin
    # permission_required = "pipelines.view_pipeline"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "evaluations",
            "title": "Evaluation Runs",
            "allow_new": False,
            "table_url": reverse("evaluations:evaluation_runs_table", args=[team_slug, kwargs["evaluation_pk"]]),
        }


class EvaluationRunTableView(SingleTableView, PermissionRequiredMixin):
    # permission_required = "pipelines.view_pipelinerun"
    model = EvaluationRun
    paginate_by = 25
    table_class = EvaluationRunTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return EvaluationRun.objects.filter(config_id=self.kwargs["evaluation_pk"]).order_by("-created_at")


class EvaluationResultHome(LoginAndTeamRequiredMixin, TemplateView):  # , PermissionRequiredMixin
    # permission_required = "pipelines.view_pipeline"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "evaluations",
            "title": "Evaluation Run Result",
            "allow_new": False,
            "table_url": reverse(
                "evaluations:evaluation_results_table",
                args=[team_slug, kwargs["evaluation_pk"], kwargs["evaluation_run_pk"]],
            ),
            # "title_help_content": render_help_with_link(
            #     "Pipelines allow you to create more complex bots by combining one or more steps together.", "pipelines"  # noqa
            # ),
        }


class EvaluationResultTableView(SingleTableView):
    template_name = "table/single_table.html"

    def get_queryset(self) -> EvaluationRun:
        return get_object_or_404(
            EvaluationRun.objects.filter(team__slug=self.kwargs["team_slug"]),
            pk=self.kwargs["evaluation_run_pk"],
        )

    def get_table_data(self):
        return self.get_queryset().get_table_data()

    def get_table_class(self):
        """
        Inspect the first row’s keys and build a Table subclass
        with one Column per field.
        """
        data = self.get_table_data()
        if not data:
            return type("EmptyTable", (tables.Table,), {})

        attrs = {}
        for row in data:
            for key in row:
                if key in attrs:
                    continue
                header = key.replace("_", " ").title()
                attrs[key] = columns.Column(verbose_name=header)

        return type("EvaluationResultTableTable", (tables.Table,), attrs)


def create_evaluation_run(request, team_slug, evaluation_pk):
    # TODO: Assert all the permissions, etc.
    config = get_object_or_404(EvaluationConfig, team__slug=team_slug, pk=evaluation_pk)
    config.run()
    return JsonResponse({"success": "true"})
