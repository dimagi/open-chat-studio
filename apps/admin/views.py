import functools
import hmac
import logging
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.conf import settings
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

from apps.admin.forms import (
    DateRangeForm,
    DateRanges,
    FindProviderByKeyForm,
    FlagUpdateForm,
    OcsConfigurationForm,
    TeamMetadataImportForm,
)
from apps.admin.imports import import_team_metadata_from_csv
from apps.admin.models import OcsConfiguration
from apps.admin.provider_keys import get_provider_key_fingerprints
from apps.admin.queries import (
    build_usage_report,
    get_message_stats,
    get_participant_stats,
    get_period_totals,
    get_platform_breakdown,
    get_team_activity_summary,
    get_top_experiments,
    get_top_teams,
    get_whatsapp_message_stats,
    get_whatsapp_numbers,
    team_metadata_to_csv,
    top_experiments_to_csv,
    top_teams_to_csv,
    usage_to_csv,
    whatsapp_message_stats_to_csv,
)
from apps.admin.serializers import StatsSerializer
from apps.service_providers.usages import get_provider_usages, search_providers_by_api_key
from apps.teams.flags import get_all_flag_info
from apps.teams.metadata import get_team_metadata_fields
from apps.teams.models import Flag, Team

logger = logging.getLogger("ocs.admin")

User = get_user_model()

is_staff = user_passes_test(lambda u: u.is_staff, login_url="/404")
is_superuser = user_passes_test(lambda u: u.is_superuser, login_url="/404")


def _has_valid_reporting_token(request):
    """True if the request carries the configured provider-reporting bearer token."""
    token = settings.PROVIDER_REPORTING_API_TOKEN
    if not token:
        return False
    prefix = "Bearer "
    header = request.headers.get("Authorization", "")
    if not header.startswith(prefix):
        return False
    return hmac.compare_digest(header.removeprefix(prefix).encode("utf-8"), token.encode("utf-8"))


def superuser_or_reporting_token(view_func):
    """Allow a valid reporting token, else fall back to the superuser-session check.

    Lets headless consumers authenticate with the shared token while the browser
    admin UI keeps working via the session (same 302-to-/404 for everyone else).
    """

    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if _has_valid_reporting_token(request):
            return view_func(request, *args, **kwargs)
        return is_superuser(view_func)(request, *args, **kwargs)

    return _wrapped


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


def _validated_range(request):
    """Return (start, end, start_timestamp, end_timestamp) for the request, or None if invalid.

    Used by the dashboard skeleton and each section endpoint so they share a single
    interpretation of the date-range form.
    """
    form = _get_form(request)
    if not form.is_valid():
        return None
    start, end = form.get_date_range()
    start_timestamp, end_timestamp = _make_aware_range(start, end)
    return start, end, start_timestamp, end_timestamp


@is_staff
def usage_chart(request):
    """Render the dashboard skeleton: export buttons plus placeholders that lazy-load each section.

    This view runs no aggregation queries, so it returns immediately even when the
    underlying data is expensive. Each section below loads independently, so a slow
    or failing section can't take down the rest of the dashboard (or the buttons).
    """
    form = _get_form(request)
    if not form.is_valid():
        return redirect("ocs_admin:home")

    start, end = form.get_date_range()
    url = reverse("ocs_admin:home")
    query_data = {
        "start": start,
        "end": end,
        "range_type": form.cleaned_data["range_type"],
    }
    response = TemplateResponse(request, "admin/usage_chart.html", context={})
    return push_url(response, f"{url}?{urlencode(query_data)}")


def _render_section(request, template, context_key, query_fn):
    """Validate the date range and render a single-query dashboard section.

    Returns an empty response on an invalid date range so a bad form value can't
    500 a lazy-loaded fragment.
    """
    date_range = _validated_range(request)
    if date_range is None:
        return HttpResponse("")
    _, _, start_timestamp, end_timestamp = date_range
    return TemplateResponse(request, template, context={context_key: query_fn(start_timestamp, end_timestamp)})


@is_staff
def section_growth(request):
    date_range = _validated_range(request)
    if date_range is None:
        return HttpResponse("")

    _, _, start_timestamp, end_timestamp = date_range
    # Previous period: an equal-length window ending exactly where the current one begins
    # (exclusive), so the boundary day isn't counted in both windows.
    period = end_timestamp - start_timestamp
    current_totals = get_period_totals(start_timestamp, end_timestamp)
    previous_totals = get_period_totals(start_timestamp - period, start_timestamp)
    return TemplateResponse(
        request,
        "admin/sections/growth.html",
        context={"growth_metrics": _compute_growth(current_totals, previous_totals)},
    )


@is_staff
def section_team_activity(request):
    return _render_section(request, "admin/sections/team_activity.html", "team_activity", get_team_activity_summary)


@is_staff
def section_charts(request):
    date_range = _validated_range(request)
    if date_range is None:
        return HttpResponse("")

    start, end, start_timestamp, end_timestamp = date_range
    usage_data = StatsSerializer(get_message_stats(start_timestamp, end_timestamp), many=True)
    participant_data = StatsSerializer(get_participant_stats(start_timestamp, end_timestamp), many=True)
    return TemplateResponse(
        request,
        "admin/sections/charts.html",
        context={
            "chart_data": {
                "message_data": usage_data.data,
                "participant_data": participant_data.data,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        },
    )


@is_staff
def section_top_teams(request):
    return _render_section(request, "admin/sections/top_teams.html", "top_teams", get_top_teams)


@is_staff
def section_platform(request):
    return _render_section(request, "admin/sections/platform.html", "platform_breakdown", get_platform_breakdown)


@is_staff
def section_top_experiments(request):
    return _render_section(request, "admin/sections/top_experiments.html", "top_experiments", get_top_experiments)


@is_staff
def section_whatsapp(request):
    return _render_section(request, "admin/sections/whatsapp.html", "whatsapp_stats", get_whatsapp_message_stats)


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


@is_staff
def export_team_metadata(request):
    response = HttpResponse(team_metadata_to_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="team_metadata.csv"'
    return response


@is_staff
def import_team_metadata(request):
    form = TeamMetadataImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        result = import_team_metadata_from_csv(form.cleaned_data["file"])
    return TemplateResponse(
        request,
        "admin/import_team_metadata.html",
        context={
            "active_tab": "admin",
            "form": form,
            "result": result,
            "metadata_fields": get_team_metadata_fields(),
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


@superuser_or_reporting_token
def provider_usage_api(request):
    """Cross-team LLM usage over a date range: per-team token totals merged with
    per-model cost detail where cost tracking is enabled. Requires `range_type`,
    `start`, and `end` query params (as the dashboard date-range form).
    """
    result = _validated_range(request)
    if result is None:
        return JsonResponse({"error": "Invalid or missing date range (range_type, start, end)"}, status=400)
    _, _, start_timestamp, end_timestamp = result
    return JsonResponse(build_usage_report(start_timestamp, end_timestamp))


@superuser_or_reporting_token
def provider_keys_api(request):
    """Masked API-key fingerprint → team mapping across all LLM providers, so a
    report can attribute provider-side cost (keyed by the provider's redacted
    key) back to the owning team. Never returns the raw secret.
    """
    return JsonResponse({"providers": list(get_provider_key_fingerprints())})


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


@is_staff
def find_provider_by_key(request):
    """Staff tool: paste an API key + pick a provider type to find usages.

    Iterates providers of the chosen type, decrypts each one's config and
    compares the configured secret fields against the supplied key.
    """
    form = FindProviderByKeyForm(request.POST or None)
    results = None
    if request.method == "POST" and form.is_valid():
        service_provider = form.cleaned_provider()
        providers = search_providers_by_api_key(
            service_provider,
            form.cleaned_data["key"],
            match=form.cleaned_data["match"],
        )
        results = [
            {
                "provider": provider,
                "service_provider": service_provider,
                "usages": get_provider_usages(provider),
            }
            for provider in providers
        ]
    return TemplateResponse(
        request,
        "admin/find_provider_by_key.html",
        context={
            "active_tab": "admin",
            "form": form,
            "results": results,
        },
    )
