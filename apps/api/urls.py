from django.urls import include, path
from rest_framework import routers

from . import views

app_name = "api"

router = routers.SimpleRouter()
router.register(r"experiments", views.ExperimentViewSet, basename="experiment")

urlpatterns = [
    path("participants/<str:participant_id>/", views.update_participant_data, name="update-participant-data"),
    path("", include(router.urls)),
]
