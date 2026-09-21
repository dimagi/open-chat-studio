from functools import update_wrapper

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from apps.web.elevation import Grant, elevation_redirect


class OcsAdminSite(admin.AdminSite):
    site_title = "OCS site admin"
    site_header = "OCS Administration"
    index_title = "OCS site admin"

    def admin_view(self, view, cacheable=False):
        """Override the admin_view method to check for temporary superuser access."""

        def inner(request, *args, **kwargs):
            if not self.has_permission(request):
                if request.path == reverse("admin:logout", current_app=self.name):
                    index_path = reverse("admin:index", current_app=self.name)
                    return HttpResponseRedirect(index_path)

                # Inner import to prevent django.contrib.admin (app) from
                # importing django.contrib.auth.models.User (unrelated model).
                from django.contrib.auth.views import redirect_to_login  # noqa: PLC0415 - lazy: avoid auth import

                return redirect_to_login(
                    request.get_full_path(),
                    reverse("admin:login", current_app=self.name),
                )

            # `has_permission` is `is_active and is_staff`, so the elevation check covers staff too.
            if redirect := elevation_redirect(request, Grant.DJANGO_ADMIN):
                return redirect

            return view(request, *args, **kwargs)

        if not cacheable:
            inner = never_cache(inner)
        # We add csrf_protect here so this function can be used as a utility
        # function for any view, without having to repeat 'csrf_protect'.
        if not getattr(view, "csrf_exempt", False):
            inner = csrf_protect(inner)
        return update_wrapper(inner, view)
