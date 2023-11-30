from django.urls import path

from apps.analysis import views

app_name = "analysis"

urlpatterns = [
    path("", views.analysis_home, name="home"),
    path("new/", views.CreateAnalysisPipeline.as_view(), name="new"),
    path("<int:pk>/", views.analysis_details, name="details"),
    path("<int:pk>/edit/", views.EditAnalysisPipeline.as_view(), name="edit"),
    path("<int:pk>/delete/", views.delete_analysis, name="delete"),
    path("<int:pk>/runs_table/", views.AnalysisRunTableView.as_view(), name="runs_table"),
    path("<int:pk>/run/", views.create_analysis_run, name="create_run"),
    path("run/<int:pk>/", views.run_details, name="run_details"),
    path("run/<int:pk>/progress/", views.run_progress, name="run_progress"),
    path("table/", views.AnalysisTableView.as_view(), name="table"),
    path("file/<int:pk>/", views.download_resource, name="download_resource"),
]
