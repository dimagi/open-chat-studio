from django.apps import AppConfig
from django.contrib import admin
from django.contrib.admin.apps import AdminConfig


class WebConfig(AppConfig):
    name = "apps.web"
    label = "web"

    def ready(self):
        from apps.utils.django_admin import export_as_csv  # noqa: PLC0415 - deferred until app registry is ready

        admin.site.add_action(export_as_csv, "Export as CSV")


class OcsAdminConfig(AdminConfig):
    default_site = "apps.web.admin.OcsAdminSite"
