from django.urls import path

from apps.help import views

app_name = "help"

urlpatterns = [
    path("<str:agent_name>/", views.run_agent, name="run_agent"),
]
