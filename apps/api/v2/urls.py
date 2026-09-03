from django.urls import include, path
from rest_framework import routers

from apps.api.v2 import views
from apps.api.v2.channels import TriggerBotMessageView
from apps.api.v2.discovery import (
    ChatbotOptionsView,
    PipelineNodeOptionsView,
    PipelineNodesView,
    PipelineNodeView,
    PipelineOptionsView,
)
from apps.api.v2.pipeline_edit.views import PipelineNodeEditView
from apps.api.v2.usage.views import UsageView

app_name = "v2"

router = routers.SimpleRouter()
router.register(r"chatbots", views.ChatbotViewSet, basename="chatbot")

# The v2 API surface: the renamed chatbot surface and all new endpoints (e.g. inspect).
# Mounted under the capturing ``v2/`` prefix; unlike v1 there is no unversioned alias.
urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("usage/", UsageView.as_view(), name="usage"),
    path("trigger_bot/", TriggerBotMessageView.as_view(), name="trigger_bot"),
    path("chatbot/options/", ChatbotOptionsView.as_view(), name="chatbot-options"),
    path("pipeline/nodes/", PipelineNodesView.as_view(), name="pipeline-nodes"),
    path("pipeline/nodes/<str:node_type>/", PipelineNodeView.as_view(), name="pipeline-node"),
    path("pipeline/options/", PipelineOptionsView.as_view(), name="pipeline-options"),
    path("pipeline/options/<str:node_type>/", PipelineNodeOptionsView.as_view(), name="pipeline-node-options"),
    # Before the router so they win over its ``chatbots/{id}/`` detail route. One view serves both,
    # so each route keeps only the verbs it offers -- else a PATCH to the collection would reach
    # ``patch`` with no ``node_id`` and raise instead of 405ing.
    path(
        "chatbots/<str:id>/pipeline/nodes/",
        PipelineNodeEditView.as_view(http_method_names=["post", "options"]),
        name="pipeline-node-create",
    ),
    path(
        "chatbots/<str:id>/pipeline/nodes/<str:node_id>/",
        PipelineNodeEditView.as_view(http_method_names=["patch", "delete", "options"]),
        name="pipeline-node-update",
    ),
    path("", include(router.urls)),
]
