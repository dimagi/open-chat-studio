from django.urls import path

from apps.generics.urls import make_crud_urls
from apps.participants import views

app_name = "participants"

urlpatterns = [path("<int:participant_id>/", views.SingleParticipantHome.as_view(), name="single-participant-home")]

urlpatterns.extend(make_crud_urls(views, "Participant", "participant"))
