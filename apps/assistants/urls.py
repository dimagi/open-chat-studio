from django.urls import path

from apps.assistants import views
from apps.generics.urls import make_crud_urls

app_name = "assistants"

urlpatterns = make_crud_urls(views, "OpenAiAssistant", "", delete=False) + [
    path("import/", views.ImportAssistant.as_view(), name="import"),
    path("<int:pk>/delete_local/", views.LocalDeleteOpenAiAssistant.as_view(), name="delete_local"),
    path("<int:pk>/sync/", views.SyncOpenAiAssistant.as_view(), name="sync"),
    path("<int:pk>/res/<int:resource_id>/add_file/", views.AddFileToAssistant.as_view(), name="add_file"),
    path(
        "<int:pk>/res/<int:resource_id>/delete_file/<int:file_id>/",
        views.DeleteFileFromAssistant.as_view(),
        name="remove_file",
    ),
    path("<int:pk>/syncing/", views.SyncEditingOpenAiAssistant.as_view(), name="sync_while_editing"),
    path("<int:pk>/checking_sync_status/", views.check_sync_status, name="check_sync_status"),
    path("<int:pk>/download_file/<int:file_id>/", views.download_file, name="download_file"),
]
