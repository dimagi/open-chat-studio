from django.contrib import admin

ADMIN_SLUG = "admin_site"


class OcsAdminSite(admin.AdminSite):
    def has_permission(self, request):
        return super().has_permission(request)
