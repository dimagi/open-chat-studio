from django.urls import path

from apps.generics.urls import make_crud_urls

from . import views

app_name = "evaluations"

urlpatterns = [
    path("<int:pk>/runs_table/", views.EvaluationRunsTableView.as_view(), name="runs_table"),
    path(
        "<int:evaluation_pk>/runs/<int:pk>/",
        views.EvaluationRunDetailView.as_view(),
        name="run_detail",
    ),
]

urlpatterns.extend(make_crud_urls(views, "Evaluation", delete=False))
urlpatterns.extend(make_crud_urls(views, "Evaluator", prefix="evaluator", delete=False))
