from django.urls import path

from . import views

app_name = "analysis"

urlpatterns = [
    path("", views.TranscriptAnalysisListView.as_view(), name="list"),
    path("create/<int:experiment_id>/", views.TranscriptAnalysisCreateView.as_view(), name="create"),
    path("<int:pk>/", views.TranscriptAnalysisDetailView.as_view(), name="detail"),
    path("<int:pk>/delete/", views.TranscriptAnalysisDeleteView.as_view(), name="delete"),
    path("<int:pk>/download/", views.download_analysis_results, name="download"),
]
