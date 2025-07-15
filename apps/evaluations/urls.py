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
        "<int:evaluation_pk>/evaluation_runs/<int:evaluation_run_pk>/download",
        evalutation_config_views.download_evaluation_run_csv,
        name="evaluation_run_download",
    ),
    path(
        "sessions_selection_table",
        dataset_views.DatasetSessionsSelectionTableView.as_view(),
        name="dataset_sessions_selection_list",
    ),
    path(
        "dataset/<int:dataset_id>/messages_table/",
        dataset_views.DatasetMessagesTableView.as_view(),
        name="dataset_messages_table",
    ),
    path(
        "dataset/<int:dataset_id>/add_message/",
        dataset_views.add_message_to_dataset,
        name="add_message_to_dataset",
    ),
    path(
        "message/<int:message_id>/edit_modal/",
        dataset_views.edit_message_modal,
        name="edit_message_modal",
    ),
    path(
        "message/<int:message_id>/update/",
        dataset_views.update_message,
        name="update_message",
    ),
    path(
        "message/<int:message_id>/delete/",
        dataset_views.delete_message,
        name="delete_message",
    ),
]

urlpatterns.extend(make_crud_urls(evalutation_config_views, "Evaluation", delete=False))
urlpatterns.extend(make_crud_urls(evaluator_views, "Evaluator", prefix="evaluator"))
urlpatterns.extend(make_crud_urls(dataset_views, "Dataset", prefix="dataset"))
