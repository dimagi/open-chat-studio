from django.urls import path

from apps.generics.urls import make_crud_urls
from apps.human_annotations.views import annotate_views, queue_views

app_name = "human_annotations"

urlpatterns = [
    # Queue detail & management
    path("queue/<int:pk>/detail/", queue_views.AnnotationQueueDetail.as_view(), name="queue_detail"),
    path("queue/<int:pk>/items-table/", queue_views.AnnotationQueueItemsTableView.as_view(), name="queue_items_table"),
    path("queue/<int:pk>/add-sessions/", queue_views.AddSessionsToQueue.as_view(), name="queue_add_sessions"),
    path("queue/<int:pk>/assignees/", queue_views.ManageAssignees.as_view(), name="queue_manage_assignees"),
    path("queue/<int:pk>/export/", queue_views.ExportAnnotations.as_view(), name="queue_export"),
    # Session-side add to queue
    path(
        "sessions/<str:session_id>/add-to-queue/",
        queue_views.AddSessionToQueueFromSession.as_view(),
        name="session_add_to_queue",
    ),
    # Annotation
    path("queue/<int:pk>/annotate/", annotate_views.AnnotateQueue.as_view(), name="annotate_queue"),
    path("queue/<int:pk>/item/<int:item_pk>/", annotate_views.AnnotateItem.as_view(), name="annotate_item"),
    path(
        "queue/<int:pk>/item/<int:item_pk>/submit/",
        annotate_views.SubmitAnnotation.as_view(),
        name="submit_annotation",
    ),
    path("queue/<int:pk>/item/<int:item_pk>/flag/", annotate_views.FlagItem.as_view(), name="flag_item"),
    path("queue/<int:pk>/item/<int:item_pk>/unflag/", annotate_views.UnflagItem.as_view(), name="unflag_item"),
]

# CRUD views for queues
urlpatterns.extend(make_crud_urls(queue_views, "AnnotationQueue", "queue"))
