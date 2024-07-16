from datetime import datetime, timedelta

from django.contrib.auth.decorators import user_passes_test
from django.template.response import TemplateResponse
from django.utils import timezone

from apps.admin.forms import DateRangeForm
from apps.admin.queries import get_message_stats
from apps.admin.serializers import StatsSerializer
from apps.users.models import CustomUser


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def admin_home(request):
    end = _get_date_param(request, "end", timezone.now().date())
    start = _get_date_param(request, "start", end - timedelta(days=90))

    serializer = StatsSerializer(get_message_stats(start, end), many=True)

    form = DateRangeForm(initial={"start": start, "end": end})
    start_value = CustomUser.objects.filter(date_joined__lt=start).count()
    return TemplateResponse(
        request,
        "admin/home.html",
        context={
            "active_tab": "admin",
            "usage_data": serializer.data,
            "form": form,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "start_value": start_value,
        },
    )


def _get_date_param(request, param_name, default):
    value = request.GET.get(param_name)
    if value:
        return _string_to_date(value)
    return default


def _string_to_date(date_str: str) -> datetime.date:
    date_format = "%Y-%m-%d"
    return datetime.strptime(date_str, date_format).date()
