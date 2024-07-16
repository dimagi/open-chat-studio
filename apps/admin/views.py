from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render


@user_passes_test(lambda u: u.is_superuser)
def admin_home(request):
    return render(request, "admin/home.html", context={})
