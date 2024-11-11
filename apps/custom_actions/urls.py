from ..generics.urls import make_crud_urls
from . import views

app_name = "custom_actions"

urlpatterns = make_crud_urls(views, "CustomAction")
