from django.urls import path

from . import views

app_name = "dashboard"
urlpatterns = [
    # Main dashboard page
    path("", views.DashboardView.as_view(), name="index"),
    # API endpoints for chart data
    path("api/overview/", views.OverviewStatsApiView.as_view(), name="api_overview"),
    path("api/active-participants/", views.ActiveParticipantsApiView.as_view(), name="api_active_participants"),
    path("api/session-analytics/", views.SessionAnalyticsApiView.as_view(), name="api_session_analytics"),
    path("api/message-volume/", views.MessageVolumeApiView.as_view(), name="api_message_volume"),
    path("api/bot-performance/", views.BotPerformanceApiView.as_view(), name="api_bot_performance"),
    path("api/user-engagement/", views.UserEngagementApiView.as_view(), name="api_user_engagement"),
    path("api/channel-breakdown/", views.ChannelBreakdownApiView.as_view(), name="api_channel_breakdown"),
    path("api/tag-analytics/", views.TagAnalyticsApiView.as_view(), name="api_tag_analytics"),
    # Filter management
    path("filters/save/", views.SaveFilterView.as_view(), name="save_filter"),
    path("filters/load/<int:filter_id>/", views.LoadFilterView.as_view(), name="load_filter"),
]
