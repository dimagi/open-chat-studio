from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from apps.admin.forms import DateRangeForm, DateRanges
from apps.admin.queries import get_message_stats, get_participant_stats, get_whatsapp_numbers, usage_to_csv
from apps.admin.serializers import StatsSerializer
from apps.experiments.models import Participant


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def admin_home(request):
    return TemplateResponse(
        request,
        "admin/home.html",
        context={
            "active_tab": "admin",
            "form": _get_form(request),
        },
    )


def _get_form(request):
    data = {field_name: request.GET.get(field_name) for field_name in DateRangeForm.declared_fields}
    if all(data.values()):
        return DateRangeForm(data)

    end = _get_date_param(request, "end", timezone.now().date())
    start = _get_date_param(request, "start", end - timedelta(days=30))
    return DateRangeForm(initial={"range_type": DateRanges.LAST_30_DAYS, "start": start, "end": end})


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def usage_chart(request):
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    end_timestamp = datetime.combine(end, time.max)
    usage_data = StatsSerializer(get_message_stats(start, end_timestamp), many=True)
    participant_data = StatsSerializer(get_participant_stats(start, end_timestamp), many=True)
    url = reverse("ocs_admin:home")
    query_data = {
        "start": start,
        "end": end,
        "range_type": form.cleaned_data["range_type"],
    }
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
        headers={"HX-Push-Url": f"{url}?{urlencode(query_data)}"},
    )


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def export_usage(request):
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    end_timestamp = datetime.combine(end, time.max)

    response = HttpResponse(usage_to_csv(start, end_timestamp), content_type="text/csv")
    export_filename = f"usage_{start.isoformat()}_{end.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{export_filename}"'
    return response


@user_passes_test(lambda u: u.is_staff, login_url="/404")
def export_whatsapp(request):
    response = HttpResponse(get_whatsapp_numbers(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="whatsapp_numbers.csv"'
    return response


def _get_date_param(request, param_name, default):
    value = request.GET.get(param_name)
    if value:
        return _string_to_date(value)
    return default


def _string_to_date(date_str: str) -> datetime.date:
    date_format = "%Y-%m-%d"
    return datetime.strptime(date_str, date_format).date()
