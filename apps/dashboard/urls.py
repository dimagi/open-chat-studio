from django.urls import path

from . import engagement_views, views

app_name = "dashboard"
urlpatterns = [
    # Main dashboard page
    path("", views.DashboardView.as_view(), name="index"),
    # Engagement dashboard (standalone page, #2927)
    path("engagement/", engagement_views.EngagementDashboardView.as_view(), name="engagement"),
    path("api/engagement/summary/", engagement_views.EngagementSummaryApiView.as_view(), name="api_engagement_summary"),
    path(
        "api/engagement/frequency/",
        engagement_views.EngagementFrequencyApiView.as_view(),
        name="api_engagement_frequency",
    ),
    path(
        "api/engagement/new-vs-returning/",
        engagement_views.NewVsReturningApiView.as_view(),
        name="api_new_vs_returning",
    ),
    path(
        "api/engagement/session-duration/",
        engagement_views.AverageSessionDurationApiView.as_view(),
        name="api_average_session_duration",
    ),
    # API endpoints for chart data
    path("api/overview/", views.OverviewStatsApiView.as_view(), name="api_overview"),
    path("api/session-analytics/", views.SessionAnalyticsApiView.as_view(), name="api_session_analytics"),
    path("api/message-volume/", views.MessageVolumeApiView.as_view(), name="api_message_volume"),
    path("api/bot-performance/", views.BotPerformanceApiView.as_view(), name="api_bot_performance"),
    path("api/user-engagement/", views.UserEngagementApiView.as_view(), name="api_user_engagement"),
    path("api/channel-breakdown/", views.ChannelBreakdownApiView.as_view(), name="api_channel_breakdown"),
    path("api/tag-analytics/", views.TagAnalyticsApiView.as_view(), name="api_tag_analytics"),
    path("api/average-response-time/", views.AverageResponseTimeApiView.as_view(), name="api_average_response_time"),
    path("api/cost-tracking-panel/", views.CostTrackingPanelView.as_view(), name="api_cost_tracking_panel"),
    path("api/cost-timeseries/", views.CostTrackingApiView.as_view(), name="api_cost_timeseries"),
    path("api/cost-breakdown/", views.CostBreakdownApiView.as_view(), name="api_cost_breakdown"),
    path("api/cost-p95/", views.CostP95ApiView.as_view(), name="api_cost_p95"),
    # Filter management
    path("filters/save/", views.SaveFilterView.as_view(), name="save_filter"),
    path("filters/load/<int:filter_id>/", views.LoadFilterView.as_view(), name="load_filter"),
    path("filters/delete/<int:filter_id>/", views.DeleteFilterView.as_view(), name="delete_filter"),
]
