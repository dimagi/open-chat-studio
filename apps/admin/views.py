from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.admin.forms import DateRangeForm, DateRanges
from apps.admin.queries import get_message_stats, get_participant_stats, get_whatsapp_numbers, usage_to_csv
from apps.admin.serializers import StatsSerializer
from apps.experiments.models import Participant
from apps.teams.models import Flag, Team

User = get_user_model()

is_staff = user_passes_test(lambda u: u.is_staff, login_url="/404")
is_superuser = user_passes_test(lambda u: u.is_superuser, login_url="/404")


@is_staff
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


@is_staff
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


@is_staff
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


@is_staff
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


@is_superuser
def flags_home(request):
    flags = Flag.objects.prefetch_related("teams", "users").all().order_by("name")
    return TemplateResponse(
        request,
        "admin/flags/home.html",
        context={
            "active_tab": "flags",
            "flags": flags,
        },
    )


@is_superuser
def flag_detail(request, flag_id):
    flag = get_object_or_404(Flag, id=flag_id)

    return TemplateResponse(
        request,
        "admin/flags/detail.html",
        context={
            "active_tab": "flags",
            "flag": flag,
        },
    )


@is_superuser
def teams_api(request):
    query = request.GET.get("q", "").strip()
    teams = Team.objects.all()

    if query:
        teams = teams.filter(name__icontains=query)

    teams = teams.order_by("name")[:20]  # Limit to 20 results

    data = [{"value": team.id, "text": team.name} for team in teams]
    return JsonResponse(data, safe=False)


@is_superuser
def users_api(request):
    query = request.GET.get("q", "").strip()
    users = User.objects.all()

    if query:
        users = users.filter(email__icontains=query)

    users = users.order_by("username")[:20]  # Limit to 20 results

    data = [{"value": user.id, "text": user.username} for user in users]
    return JsonResponse(data, safe=False)


@is_superuser
@require_http_methods(["POST"])
def update_flag(request, flag_id):
    flag = get_object_or_404(Flag, id=flag_id)

    try:
        with transaction.atomic():
            flag.everyone = request.POST.get("everyone") == "on"
            flag.testing = request.POST.get("testing") == "on"
            flag.superusers = request.POST.get("superusers") == "on"
            flag.rollout = request.POST.get("rollout") == "on"

            percent_str = request.POST.get("percent", "").strip()
            if percent_str:
                try:
                    percent = max(0, min(100, int(float(percent_str))))
                    flag.percent = percent
                except (ValueError, TypeError):
                    flag.percent = None
            else:
                flag.percent = None

            team_ids = request.POST.getlist("teams")
            teams = Team.objects.filter(id__in=team_ids)
            flag.teams.set(teams)

            user_ids = request.POST.getlist("users")
            users = User.objects.filter(id__in=user_ids)
            flag.users.set(users)

            flag.save()

        return JsonResponse({"success": True})
    except ValidationError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:
        return JsonResponse({"error": "Failed to update flag"}, status=500)
