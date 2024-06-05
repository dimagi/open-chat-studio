from apps.generics.urls import make_crud_urls
from apps.participants import views

app_name = "participants"

urlpatterns = []
urlpatterns.extend(make_crud_urls(views, "Participant", "participant"))
