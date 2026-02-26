from django.urls import path

from . import views

app_name = "trace"
urlpatterns = [
    path("home/", views.TracesHome.as_view(), name="home"),
    path("table/", views.TraceTableView.as_view(), name="table"),
    path("<int:pk>/", views.TraceDetailView.as_view(), name="trace_detail"),
    path("<int:pk>/langfuse-spans/", views.TraceLangfuseSpansView.as_view(), name="trace_langfuse_spans"),
]
