from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http.response import HttpResponse as HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View
from django_tables2 import SingleTableView

from apps.experiments.filters import get_filter_context_data
from apps.filters.models import FilterSet
from apps.generics import actions
from apps.ocs_notifications.filters import (
    SeverityLevelFilter,
    TeamFilter,
    UserNotificationFilter,
    build_toggle_options,
    resolve_notification_filter_params,
)
from apps.ocs_notifications.models import EventType, EventUser, NotificationEvent, UserNotificationPreferences
from apps.ocs_notifications.tables import NotificationEventTable, UserNotificationTable
from apps.ocs_notifications.utils import (
    TIMEDELTA_MAP,
    bust_unread_notification_cache,
    mute_notification,
    toggle_notification_read,
    unmute_notification,
)
from apps.utils.tables import render_table_row

# Below this many teams, the team filter renders as toggle buttons; at or above it, users rely
# on the dropdown multi-select in the shared filter panel instead.
TEAM_TOGGLE_BUTTON_THRESHOLD = 3


def get_notification_toggle_context(request) -> dict:
    """Build the level/team toggle-button options for the notifications list (see
    build_toggle_options). Shared by the page's initial render and by the table endpoint's own
    response, so every click re-renders the buttons with the query strings/active state that
    match the filter it just applied -- htmx swaps this back in out-of-band alongside the table
    (see notification_table_with_toggles.html), otherwise a button's own href would stay frozen
    at whatever it was on the last full page load and a second click could never undo the first.
    """
    # Not request.path: this context is also built while rendering the table endpoint's own
    # response (for the out-of-band button refresh), and the pushed URL must always be the
    # notifications page, not wherever the request that built it happened to land.
    context = {"notifications_home_url": reverse("ocs_notifications:notifications_home")}
    level_filter = SeverityLevelFilter().model_copy(deep=True)
    context["level_toggle_options"] = build_toggle_options(level_filter, request)

    team_filter = TeamFilter().model_copy(deep=True)
    team_filter.prepare(request.team, user=request.user)
    if len(team_filter.options) < TEAM_TOGGLE_BUTTON_THRESHOLD:
        context["team_toggle_options"] = build_toggle_options(team_filter, request)

    return context


def get_do_not_disturb_context(user) -> dict:
    """Build the context needed to render the Do Not Disturb widget: the user's teams (for
    the "silence" modal's team picker) and which of those teams are currently silenced.

    Shared by ``NotificationHome`` and the set/cancel views so all three render the widget
    from the same up-to-date data after every change.
    """
    teams = list(user.teams.all().order_by("name"))
    silenced_preferences = list(
        UserNotificationPreferences.objects.filter(user=user, team__in=teams, do_not_disturb_until__gt=timezone.now())
        .select_related("team")
        .order_by("team__name")
    )
    return {"teams": teams, "silenced_preferences": silenced_preferences}


class NotificationHome(LoginRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        table_url = reverse("ocs_notifications:notifications_table")
        do_not_disturb_context = get_do_not_disturb_context(self.request.user)

        context = {
            "active_tab": "notifications",
            "title": "Notifications",
            "table_url": table_url,
            "enable_search": False,
            # Currently-silenced teams can grow to any number/length, so they're rendered in
            # the (full-width) filter bar row rather than the cramped top-right toolbar --
            # see notification_filter_bar_actions.html.
            "filter_bar_action": "ocs_notifications/components/notification_filter_bar_actions.html",
            "mark_all_read_url": reverse("ocs_notifications:mark_all_notifications_read"),
            "actions": [
                actions.Action(
                    url_name="ocs_notifications:set_do_not_disturb",
                    url_factory=lambda url_name, _request, _record, _value: reverse(url_name),
                    template="ocs_notifications/components/do_not_disturb_button.html",
                    extra_context=do_not_disturb_context,
                ),
                actions.Action(
                    url_name="users:user_profile",
                    url_factory=lambda url_name, _request, _record, _value: reverse(url_name),
                    label="Preferences",
                    icon_class="fa fa-cog",
                ),
            ],
        }
        context.update(do_not_disturb_context)
        context.update(get_notification_toggle_context(self.request))

        # Add filter context
        columns = UserNotificationFilter.columns(team=self.request.team, user=self.request.user)
        filter_context = get_filter_context_data(
            team=self.request.team,
            columns=columns,
            filter_class=UserNotificationFilter,
            table_url=table_url,
            table_container_id="data-table",
            table_type=FilterSet.TableType.NOTIFICATIONS,
        )
        context.update(filter_context)

        return context


def get_filtered_user_notifications(request):
    """Build the current user's cross-team notifications queryset, narrowed by whatever
    filters (including the team filter) are active in the requesting view.

    Shared by ``UserNotificationTableView`` and ``MarkAllNotificationsReadView`` so that
    "mark all read" always acts on exactly the rows currently displayed in the table.
    """
    queryset = (
        EventUser.objects.with_latest_event()
        .with_mute_status()
        .with_event_count()
        .filter(user=request.user, team__in=request.user.teams.all())
        .select_related("event_type", "team")
        .filter(latest_event_created_at__isnull=False)
    )

    notification_filter = UserNotificationFilter()
    filter_params = resolve_notification_filter_params(request)
    user_timezone = request.session.get("detected_tz")

    return notification_filter.apply(queryset, filter_params=filter_params, timezone=user_timezone)


class UserNotificationTableView(LoginRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    model = EventUser
    table_class = UserNotificationTable
    template_name = "ocs_notifications/components/notification_table_with_toggles.html"

    def get_queryset(self):
        return get_filtered_user_notifications(self.request).order_by("-latest_event_created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["table_url"] = reverse("ocs_notifications:notifications_table")
        context.update(get_notification_toggle_context(self.request))
        return context


class ToggleNotificationReadView(LoginRequiredMixin, View):
    def post(self, request, notification_id: int, *args, **kwargs):
        event_user = get_object_or_404(
            EventUser.objects.with_latest_event().with_mute_status(),
            id=notification_id,
            user=self.request.user,
            team__in=request.user.teams.all(),
        )

        toggle_notification_read(user=request.user, event_user=event_user, read=not event_user.read)

        return render_table_row(request, UserNotificationTable, event_user)


class MuteNotificationView(LoginRequiredMixin, View):
    """Mute a specific notification identifier or all notifications"""

    def post(self, request, notification_id: int, *args, **kwargs):
        event_user = get_object_or_404(
            EventUser.objects.with_mute_status().select_related("event_type"),
            id=notification_id,
            user=self.request.user,
            team__in=request.user.teams.all(),
        )

        # Get duration from POST data (in hours)
        duration_param = request.POST.get("duration")
        if duration_param not in TIMEDELTA_MAP:
            messages.error(request, "Invalid duration for muting notifications.")
            return render(
                request,
                "ocs_notifications/components/mute_button.html",
                context={
                    "record": event_user,
                    "is_muted": event_user.is_muted,
                    "muted_until": event_user.muted_until,
                },
            )
        event_user = mute_notification(
            user=request.user,
            team=event_user.team,
            event_type=event_user.event_type,
            timedelta=TIMEDELTA_MAP[duration_param],
        )

        return render(
            request,
            "ocs_notifications/components/mute_button.html",
            context={
                "record": event_user,
                "is_muted": True,
                "muted_until": event_user.muted_until,
            },
        )


class UnmuteNotificationView(LoginRequiredMixin, View):
    """Unmute a specific notification identifier or all notifications"""

    def post(self, request, notification_id: int, *args, **kwargs):
        user_notification = get_object_or_404(
            EventUser.objects.select_related("event_type"),
            id=notification_id,
            user=self.request.user,
            team__in=request.user.teams.all(),
        )

        unmute_notification(user=request.user, team=user_notification.team, event_type=user_notification.event_type)

        return render(
            request,
            "ocs_notifications/components/mute_button.html",
            context={"record": user_notification, "is_muted": False, "muted_until": None},
        )


class SetDoNotDisturbView(LoginRequiredMixin, View):
    """Silence notifications for a chosen set of the user's teams (or all of them)."""

    def post(self, request, *args, **kwargs):
        duration_param = request.POST.get("duration")
        if request.POST.get("all_teams"):
            teams = list(request.user.teams.all())
        else:
            team_ids = request.POST.getlist("teams")
            teams = list(request.user.teams.filter(id__in=team_ids))

        if duration_param not in TIMEDELTA_MAP:
            messages.error(request, "Invalid duration for Do Not Disturb")
        elif not teams:
            messages.error(request, "Select at least one team, or All Teams, to silence")
        else:
            until = timezone.now() + TIMEDELTA_MAP[duration_param].value
            for team in teams:
                UserNotificationPreferences.objects.update_or_create(
                    user=request.user, team=team, defaults={"do_not_disturb_until": until}
                )

        return render(
            request,
            "ocs_notifications/components/do_not_disturb_pills.html",
            get_do_not_disturb_context(request.user),
        )


class CancelDoNotDisturbView(LoginRequiredMixin, View):
    """Cancel Do Not Disturb for a single one of the user's teams."""

    def post(self, request, team_id: int, *args, **kwargs):
        team = get_object_or_404(request.user.teams, id=team_id)
        UserNotificationPreferences.objects.filter(user=request.user, team=team).update(do_not_disturb_until=None)

        return render(
            request,
            "ocs_notifications/components/do_not_disturb_pills.html",
            get_do_not_disturb_context(request.user),
        )


class MarkAllNotificationsReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        # Same filters (including the team filter) as the table currently displayed, so "mark
        # all read" only ever touches the filtered set the user is looking at.
        unread = get_filtered_user_notifications(request).filter(read=False)
        team_slugs = list(unread.values_list("team__slug", flat=True).distinct())
        unread.update(read=True, read_at=timezone.now())

        for team_slug in team_slugs:
            bust_unread_notification_cache(user_id=request.user.id, team_slug=team_slug)

        table_url = reverse("ocs_notifications:notifications_table")
        return render(request, "ocs_notifications/components/mark_all_read_reload.html", {"table_url": table_url})


class NotificationEventHome(LoginRequiredMixin, TemplateView):
    template_name = "ocs_notifications/notification_event_home.html"

    @cached_property
    def event_type(self) -> EventType:
        return get_object_or_404(
            EventType.objects.select_related("team"),
            team__in=self.request.user.teams.all(),
            id=self.kwargs["event_type_id"],
        )

    def get(self, request, *args, **kwargs) -> HttpResponse:
        # Clicking the event marks it as read. We explicitly don't use the toggle_notification_read function here to
        # avoid multiple DB queries
        EventUser.objects.filter(
            user=self.request.user, team=self.event_type.team, event_type=self.event_type, read=False
        ).update(read=True, read_at=timezone.now())
        bust_unread_notification_cache(user_id=self.request.user.id, team_slug=self.event_type.team.slug)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        table_url = reverse("ocs_notifications:notification_event_table", args=[self.event_type.id])

        title = self.event_type.notificationevent_set.order_by("-created_at").values_list("title", flat=True).first()
        context = {
            "active_tab": "notifications",
            "title": "Notifications",
            "subtitle": title or "",
            "table_url": table_url,
            "enable_search": False,
            # Notifications are cross-team (unlike most detail pages, this one isn't scoped to
            # request.team), so the breadcrumb needs its own team indicator.
            "team": self.event_type.team,
        }

        return context


class NotificationEventTableView(LoginRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    model = NotificationEvent
    table_class = NotificationEventTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            NotificationEvent.objects.filter(
                team__in=self.request.user.teams.all(), event_type_id=self.kwargs["event_type_id"]
            )
            .select_related("event_type")
            .order_by("-created_at")
        )
