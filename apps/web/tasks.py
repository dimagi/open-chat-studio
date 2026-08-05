import logging

from celery import shared_task
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.utils import timezone

from apps.utils.celery import Queues

logger = logging.getLogger("ocs.web.tasks")

#: Expired sessions are deleted in chunks this size. Django's own ``clearsessions``
#: issues a single unbounded ``DELETE``, which on a large backlog means one long
#: transaction and a matching spike in WAL.
SESSION_CLEANUP_BATCH_SIZE = 10_000


@shared_task(queue=Queues.BACKGROUND)
def cleanup_silk_data():
    call_command("silk_request_garbage_collect")


@shared_task(ignore_result=True, queue=Queues.BACKGROUND)
def clear_expired_sessions():
    """Delete session rows whose ``expire_date`` has passed.

    Django never prunes these on its own — the db session backend only writes
    rows and relies on ``clearsessions`` being run periodically, so without this
    the table grows for the lifetime of the deployment.

    Deletes in batches rather than calling ``clearsessions``: the command's
    single ``DELETE`` is fine once the table is kept trimmed, but not for
    working through an accumulated backlog on a live database.
    """
    now = timezone.now()
    deleted_total = 0
    while True:
        expired_ids = list(
            Session.objects.filter(expire_date__lt=now).values_list("session_key", flat=True)[
                :SESSION_CLEANUP_BATCH_SIZE
            ]
        )
        if not expired_ids:
            break
        deleted_count, _ = Session.objects.filter(
            session_key__in=expired_ids,
            expire_date__lt=now,
        ).delete()
        deleted_total += deleted_count

    if deleted_total:
        logger.info("Deleted %s expired sessions", deleted_total)
