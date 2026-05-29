from django.urls import path

from apps.assessments import views

app_name = "assessments"

urlpatterns = [
    path("", views.ConcordanceView.as_view(), name="concordance"),
    path("export/", views.export_concordance_csv, name="concordance_export"),
]
