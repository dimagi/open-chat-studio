from django.urls import path

from . import views

app_name = "traces"
urlpatterns = [
    path("home/", views.TracesHome.as_view(), name="home"),
    path("table/", views.TraceTableView.as_view(), name="table"),
]
