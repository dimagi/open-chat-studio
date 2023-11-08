from django.apps import AppConfig


class WebConfig(AppConfig):
    name = "apps.web"
    label = "web"

    def ready(self):
        from . import tables  # noqa: F401
