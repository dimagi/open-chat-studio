from django.urls import path

from apps.generics.urls import make_crud_urls

from .views import dataset_views, evaluator_views, evalutation_config_views

app_name = "evaluations"

urlpatterns = [
    path("<int:pk>/runs_table/", evalutation_config_views.EvaluationRunsTableView.as_view(), name="runs_table"),
    path(
        "<int:evaluation_pk>/runs/<int:pk>/",
        evalutation_config_views.EvaluationRunDetailView.as_view(),
        name="run_detail",
    ),
]

urlpatterns.extend(make_crud_urls(evalutation_config_views, "Evaluation", delete=False))
urlpatterns.extend(make_crud_urls(evaluator_views, "Evaluator", prefix="evaluator", delete=False))
urlpatterns.extend(make_crud_urls(dataset_views, "Dataset", prefix="dataset", delete=False))
