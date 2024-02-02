from django.urls import path

from apps.assistants import views
from apps.generics.urls import make_crud_urls

app_name = "assistants"

urlpatterns = make_crud_urls(views, "OpenAiAssistant", "") + [
    path("import/", views.ImportAssistant.as_view(), name="import"),
]
