from django.urls import path

from apps.generics.urls import make_crud_urls

from .views import dataset_views, evaluator_views, evalutation_config_views

app_name = "evaluations"

urlpatterns = [
    path(
        "<int:evaluation_pk>/runs/new/",
        evalutation_config_views.create_evaluation_run,
        name="create_evaluation_run",
    ),
    path(
        "<int:evaluation_pk>/evaluation_runs",
        evalutation_config_views.EvaluationRunHome.as_view(),
        name="evaluation_runs_home",
    ),
    path(
        "<int:evaluation_pk>/evaluation_runs_table",
        evalutation_config_views.EvaluationRunTableView.as_view(),
        name="evaluation_runs_table",
    ),
    path(
        "<int:evaluation_pk>/evaluation_runs/<int:evaluation_run_pk>/results",
        evalutation_config_views.EvaluationResultHome.as_view(),
        name="evaluation_results_home",
    ),
    path(
        "<int:evaluation_pk>/evaluation_runs/<int:evaluation_run_pk>/results_table",
        evalutation_config_views.EvaluationResultTableView.as_view(),
        name="evaluation_results_table",
    ),
    path(
        "sessions_table",
        dataset_views.DatasetSessionsTableView.as_view(),
        name="dataset_sessions_list",
    ),
    path(
        "sessions_selection_table",
        dataset_views.DatasetSessionsSelectionTableView.as_view(),
        name="dataset_sessions_selection_list",
    ),
    path(
        "dataset/new_from_sessions/",
        dataset_views.CreateDatasetFromSessions.as_view(),
        name="new_dataset_from_sessions",
    ),
    path("session/<str:session_id>/messages_json/", dataset_views.session_messages_json, name="session_messages_json"),
]

urlpatterns.extend(make_crud_urls(evalutation_config_views, "Evaluation", delete=False))
urlpatterns.extend(make_crud_urls(evaluator_views, "Evaluator", prefix="evaluator", delete=False))
urlpatterns.extend(make_crud_urls(dataset_views, "Dataset", prefix="dataset", delete=False))
