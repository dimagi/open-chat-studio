from django.urls import path

from . import views

app_name = "analysis"

urlpatterns = [
    path("", views.TranscriptAnalysisListView.as_view(), name="list"),
    path("create/<int:experiment_id>/", views.TranscriptAnalysisCreateView.as_view(), name="create"),
    path("<int:pk>/", views.TranscriptAnalysisDetailView.as_view(), name="detail"),
    path("<int:pk>/run/", views.run_analysis, name="run"),
    path("<int:pk>/delete/", views.TranscriptAnalysisDeleteView.as_view(), name="delete"),
    path("<int:pk>/download/", views.download_analysis_results, name="download"),
    path("<int:pk>/export-sessions/", views.export_sessions, name="export_sessions"),
    path("<int:pk>/clone/", views.clone, name="clone"),
    # HTMX update endpoints
    path("<int:pk>/update_field/", views.update_field, name="update_field"),
    path("<int:pk>/add_query/", views.add_query, name="add_query"),
    path("<int:pk>/query/<int:query_id>/", views.update_query, name="update_query"),
]
