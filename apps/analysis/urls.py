from django.urls import path

from apps.analysis import views

app_name = "analysis"

urlpatterns = [
    path("", views.analysis_home, name="home"),
    path("new/", views.CreateAnalysisPipeline.as_view(), name="new"),
    path("<int:pk>/", views.EditAnalysisPipeline.as_view(), name="edit"),
    path("<int:pk>/delete/", views.delete_analysis, name="delete"),
    path("<int:pk>/run/", views.create_analysis_run, name="create_run"),
    path("table/", views.AnalysisTableView.as_view(), name="table"),
]
