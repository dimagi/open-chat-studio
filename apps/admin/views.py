from datetime import datetime, timedelta

from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from apps.admin.forms import DateRangeForm
from apps.admin.queries import get_message_stats, get_participant_stats, usage_to_csv
from apps.admin.serializers import StatsSerializer
from apps.experiments.models import Participant


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def admin_home(request):
    end = _get_date_param(request, "end", timezone.now().date())
    start = _get_date_param(request, "start", end - timedelta(days=90))
    form = DateRangeForm(initial={"start": start, "end": end})
    return TemplateResponse(
        request,
        "admin/home.html",
        context={
            "active_tab": "admin",
            "form": form,
        },
    )


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def usage_chart(request):
    end = _get_date_param(request, "end", timezone.now().date())
    start = _get_date_param(request, "start", end - timedelta(days=90))

    usage_data = StatsSerializer(get_message_stats(start, end), many=True)
    participant_data = StatsSerializer(get_participant_stats(start, end), many=True)
    url = reverse("ocs_admin:home")
    return TemplateResponse(
        request,
        "admin/usage_chart.html",
        context={
            "chart_data": {
                "message_data": usage_data.data,
                "participant_data": {
                    "data": participant_data.data,
                    "start_value": Participant.objects.filter(created_at__lt=start).count(),
                },
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        },
        headers={"HX-Push-Url": f"{url}?start={start}&end={end}"},
    )


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def export_usage(request):
    end = _get_date_param(request, "end", timezone.now().date())
    start = _get_date_param(request, "start", end - timedelta(days=90))

    response = HttpResponse(usage_to_csv(start, end), content_type="text/csv")
    export_filename = f"usage_{start.isoformat()}_{end.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{export_filename}"'
    return response


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def export_whatsapp(request):
    pass


def _get_date_param(request, param_name, default):
    value = request.GET.get(param_name)
    if value:
        return _string_to_date(value)
    return default


def _string_to_date(date_str: str) -> datetime.date:
    date_format = "%Y-%m-%d"
    return datetime.strptime(date_str, date_format).date()
