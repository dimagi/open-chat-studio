from django.urls import path

from . import views

app_name = "filters"
urlpatterns = [
    path(
        "/<int:pk>/",
        views.FilterSetView.as_view(),
        name="filter_set",
    ),
    path(
        "filters/<str:table_type>/list/",
        views.list_filter_sets,
        name="list_filter_set",
    ),
    path(
        "filters/<str:table_type>/create/",
        views.create_filter_set,
        name="create_filter_set",
    ),
]
