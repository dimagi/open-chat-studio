from django.urls import path

from apps.analysis import views

app_name = "analysis"

urlpatterns = [
    path("", views.analysis_home, name="home"),
    path("new/", views.CreateAnalysisPipeline.as_view(), name="new"),
    path("<int:pk>/", views.analysis_details, name="details"),
    path("<int:pk>/configure/", views.analysis_configure, name="configure"),
    path("<int:pk>/edit/", views.EditAnalysisPipeline.as_view(), name="edit"),
    path("<int:pk>/delete/", views.delete_analysis, name="delete"),
    path("<int:pk>/run_groups_table/", views.RunGroupTableView.as_view(), name="runs_table"),
    path("<int:pk>/run/", views.create_analysis_run, name="create_run"),
    path("run/<int:pk>/", views.run_group_details, name="group_details"),
    path("run/<int:pk>/delete/", views.delete_run_group, name="delete_group"),
    path("run/<int:pk>/progress/", views.group_progress, name="group_progress"),
    path("run/<int:pk>/replay/", views.replay_run, name="replay_run"),
    path("run/<int:pk>/feedback/", views.group_feedback, name="group_feedback"),
    path("table/", views.AnalysisTableView.as_view(), name="table"),
    path("file/<int:pk>/", views.download_resource, name="download_resource"),
]
