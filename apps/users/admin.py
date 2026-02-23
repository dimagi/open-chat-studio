from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.humanize.templatetags.humanize import naturaltime

from apps.utils.django_admin import RelativeDateFieldListFilter

from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = [*UserAdmin.list_display, "last_login", "last_login_natural"]
    list_filter = [*UserAdmin.list_filter, ("last_login", RelativeDateFieldListFilter)]
    fieldsets = [*UserAdmin.fieldsets, ("Custom Fields", {"fields": ("avatar", "language")})]

    @admin.display(description="Last Login")
    def last_login_natural(self, obj):
        return naturaltime(obj.last_login)
