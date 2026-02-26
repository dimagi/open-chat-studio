import logging
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
from django_htmx.http import push_url
from field_audit.models import AuditEvent

from apps.admin.forms import DateRangeForm, DateRanges, FlagUpdateForm, OcsConfigurationForm
from apps.admin.models import OcsConfiguration
from apps.admin.queries import (
    get_message_stats,
    get_participant_stats,
    get_period_totals,
    get_platform_breakdown,
    get_team_activity_summary,
    get_top_experiments,
    get_top_teams,
    get_whatsapp_message_stats,
    get_whatsapp_numbers,
    top_experiments_to_csv,
    top_teams_to_csv,
    usage_to_csv,
    whatsapp_message_stats_to_csv,
)
from apps.admin.serializers import StatsSerializer
from apps.experiments.models import Participant
from apps.teams.flags import get_all_flag_info
from apps.teams.models import Flag, Team

logger = logging.getLogger("ocs.admin")

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


def _make_aware_range(start_date, end_date):
    """Convert date objects to timezone-aware datetimes spanning the full day range."""
    tz = timezone.get_current_timezone()
    return (
        datetime.combine(start_date, time.min, tzinfo=tz),
        datetime.combine(end_date, time.max, tzinfo=tz),
    )


def _get_form(request):
    data = {field_name: request.GET.get(field_name) for field_name in DateRangeForm.declared_fields}
    if all(data.values()):
        return DateRangeForm(data)

    end = _get_date_param(request, "end", timezone.now().date())
    start = _get_date_param(request, "start", end - timedelta(days=30))
    return DateRangeForm(initial={"range_type": DateRanges.LAST_30_DAYS, "start": start, "end": end})


def _compute_growth(current, previous):
    metrics = []
    for key, label in [("messages", "Messages"), ("participants", "Participants"), ("sessions", "Sessions")]:
        cur = current[key]
        prev = previous[key]
        if prev > 0:
            pct = round((cur - prev) / prev * 100, 1)
        elif cur > 0:
            pct = 100.0
        else:
            pct = 0.0
        metrics.append({"label": label, "current": cur, "previous": prev, "pct_change": pct})
    return metrics


@is_staff
def usage_chart(request):
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    start_timestamp, end_timestamp = _make_aware_range(start, end)
    usage_data = StatsSerializer(get_message_stats(start_timestamp, end_timestamp), many=True)
    participant_data = StatsSerializer(get_participant_stats(start_timestamp, end_timestamp), many=True)
    whatsapp_stats = get_whatsapp_message_stats(start_timestamp, end_timestamp)

    period_length = end - start
    prev_end = start
    prev_start = prev_end - period_length
    prev_start_timestamp, prev_end_timestamp = _make_aware_range(prev_start, prev_end)

    current_totals = get_period_totals(start_timestamp, end_timestamp)
    previous_totals = get_period_totals(prev_start_timestamp, prev_end_timestamp)
    growth_metrics = _compute_growth(current_totals, previous_totals)

    top_teams = get_top_teams(start_timestamp, end_timestamp)
    platform_breakdown = get_platform_breakdown(start_timestamp, end_timestamp)
    team_activity = get_team_activity_summary(start_timestamp, end_timestamp)
    top_experiments = get_top_experiments(start_timestamp, end_timestamp)

    url = reverse("ocs_admin:home")
    query_data = {
        "start": start,
        "end": end,
        "range_type": form.cleaned_data["range_type"],
    }
    response = TemplateResponse(
        request,
        "admin/usage_chart.html",
        context={
            "chart_data": {
                "message_data": usage_data.data,
                "participant_data": {
                    "data": participant_data.data,
                    "start_value": Participant.objects.filter(created_at__lt=start_timestamp).count(),
                },
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "whatsapp_stats": whatsapp_stats,
            "growth_metrics": growth_metrics,
            "top_teams": top_teams,
            "platform_breakdown": platform_breakdown,
            "team_activity": team_activity,
            "top_experiments": top_experiments,
        },
    )
    return push_url(response, f"{url}?{urlencode(query_data)}")


@is_staff
def export_usage(request):
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    start_timestamp, end_timestamp = _make_aware_range(start, end)

    response = HttpResponse(usage_to_csv(start_timestamp, end_timestamp), content_type="text/csv")
    export_filename = f"usage_{start.isoformat()}_{end.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{export_filename}"'
    return response


@is_staff
def export_whatsapp(request):
    response = HttpResponse(get_whatsapp_numbers(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="whatsapp_numbers.csv"'
    return response


@is_staff
def export_whatsapp_stats(request):
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    start_timestamp, end_timestamp = _make_aware_range(start, end)

    response = HttpResponse(whatsapp_message_stats_to_csv(start_timestamp, end_timestamp), content_type="text/csv")
    export_filename = f"whatsapp_stats_{start.isoformat()}_{end.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{export_filename}"'
    return response


@is_staff
def export_top_teams(request):
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    start_timestamp, end_timestamp = _make_aware_range(start, end)

    response = HttpResponse(top_teams_to_csv(start_timestamp, end_timestamp), content_type="text/csv")
    export_filename = f"top_teams_{start.isoformat()}_{end.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{export_filename}"'
    return response


@is_staff
def export_top_experiments(request):
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    start_timestamp, end_timestamp = _make_aware_range(start, end)

    response = HttpResponse(top_experiments_to_csv(start_timestamp, end_timestamp), content_type="text/csv")
    export_filename = f"top_experiments_{start.isoformat()}_{end.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{export_filename}"'
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
    flag_info_map = get_all_flag_info()

    # Separate flags into active and legacy
    active_flags = [flag for flag in flags if flag.name in flag_info_map]
    legacy_flags = [flag for flag in flags if flag.name not in flag_info_map]

    return TemplateResponse(
        request,
        "admin/flags/home.html",
        context={
            "active_tab": "flags",
            "active_flags": active_flags,
            "legacy_flags": legacy_flags,
            "flag_info_map": flag_info_map,
        },
    )


@is_superuser
def flag_detail(request, flag_name):
    flag = get_object_or_404(Flag, name=flag_name)
    flag_info_map = get_all_flag_info()

    audit_events = AuditEvent.objects.filter(object_class_path="apps.teams.models.Flag", object_pk=flag.pk).order_by(
        "-event_date"
    )[:50]  # Last 50 changes

    return TemplateResponse(
        request,
        "admin/flags/detail.html",
        context={
            "active_tab": "flags",
            "flag": flag,
            "flag_info": flag_info_map.get(flag.name),
            "audit_events": audit_events,
        },
    )


@is_superuser
def flag_history(request, flag_name):
    flag = get_object_or_404(Flag, name=flag_name)

    audit_events = AuditEvent.objects.filter(object_class_path="apps.teams.models.Flag", object_pk=flag.pk).order_by(
        "-event_date"
    )[:50]  # Last 50 changes

    # Collect all team and user IDs from audit event deltas
    team_ids = set()
    user_ids = set()

    def _collect_ids(field, delta, id_set):
        if field in delta:
            for action in ["add", "remove"]:
                if action in delta[field]:
                    id_set.update(delta[field][action])

    for event in audit_events:
        if event.delta:
            _collect_ids("teams", event.delta, team_ids)
            _collect_ids("users", event.delta, user_ids)

    # Bulk load teams and users to avoid N+1 queries
    teams_map = {team.id: team.name for team in Team.objects.filter(id__in=team_ids)}
    users_map = {user.id: user.get_display_name() for user in User.objects.filter(id__in=user_ids)}

    # Transform audit event deltas to include names instead of IDs
    def _update_delta(field, delta, display_map, default_template):
        if field in delta:
            for action in ["add", "remove"]:
                if action in delta[field]:
                    delta[field][action] = [
                        display_map.get(obj_id, default_template.format(obj_id)) for obj_id in delta[field][action]
                    ]

    for event in audit_events:
        if event.delta:
            _update_delta("teams", event.delta, teams_map, "Team {}")
            _update_delta("users", event.delta, users_map, "User {}")

    return TemplateResponse(
        request,
        "admin/flags/history_fragment.html",
        context={
            "flag": flag,
            "audit_events": audit_events,
        },
    )


@is_superuser
def teams_api(request):
    query = request.GET.get("q", "").strip()

    # Input validation for query parameter
    if len(query) > 100:  # Prevent excessively long queries
        return JsonResponse({"error": "Query too long"}, status=400)

    teams = Team.objects.all()

    if query:
        teams = teams.filter(name__icontains=query)

    teams = teams.order_by("name")[:20]  # Limit to 20 results

    data = [{"value": team.id, "text": team.name} for team in teams]
    return JsonResponse(data, safe=False)


@is_superuser
def users_api(request):
    query = request.GET.get("q", "").strip()

    # Input validation for query parameter
    if len(query) > 100:  # Prevent excessively long queries
        return JsonResponse({"error": "Query too long"}, status=400)

    users = User.objects.all()

    if query:
        users = users.filter(email__icontains=query)

    users = users.order_by("username")[:20]  # Limit to 20 results

    data = [{"value": user.id, "text": user.username} for user in users]
    return JsonResponse(data, safe=False)


@is_superuser
@require_http_methods(["POST"])
def update_flag(request, flag_name):
    flag = get_object_or_404(Flag, name=flag_name)

    form = FlagUpdateForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Invalid form data", "details": form.errors}, status=400)

    try:
        with transaction.atomic():
            flag.everyone = form.cleaned_data["everyone"]
            flag.testing = form.cleaned_data["testing"]
            flag.superusers = form.cleaned_data["superusers"]
            flag.rollout = form.cleaned_data["rollout"]
            flag.percent = form.cleaned_data["percent"]

            flag.teams.set(form.cleaned_data["teams"])
            flag.users.set(form.cleaned_data["users"])

            flag.save()

        return JsonResponse({"success": True})
    except ValidationError as e:
        return JsonResponse({"error": e.messages}, status=400)
    except Exception:
        logger.exception("Failed to update flag")
        return JsonResponse({"error": "Failed to update flag"}, status=500)


@is_superuser
@require_http_methods(["DELETE"])
def delete_flag(request, flag_name):
    flag = get_object_or_404(Flag, name=flag_name)
    flag_info_map = get_all_flag_info()

    # Only allow deletion of legacy flags (not in flag_info_map)
    if flag.name in flag_info_map:
        return HttpResponse("Cannot delete active flag", status=403)

    try:
        flag.delete()
        return HttpResponse(status=200)
    except Exception:
        logger.exception("Failed to delete flag")
        return HttpResponse("Failed to delete flag", status=500)


@is_superuser
def configuration(request):
    """View for editing the single OcsConfiguration instance."""
    # Get or create the single configuration instance
    config_instance = OcsConfiguration.objects.first()

    if request.method == "POST":
        form = OcsConfigurationForm(request.POST, instance=config_instance)
        if form.is_valid():
            form.save()
            return redirect("ocs_admin:configuration")
    else:
        form = OcsConfigurationForm(instance=config_instance)

    return TemplateResponse(
        request,
        "admin/configuration.html",
        context={
            "active_tab": "configuration",
            "form": form,
            "config_instance": config_instance,
        },
    )
