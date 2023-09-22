import logging
import time
from contextlib import contextmanager
from functools import wraps

import redis
from django.conf import settings

LOCK_EXPIRE = 60 * 5  # 5 minutes
logger = logging.getLogger(__name__)


@contextmanager
def redis_task_lock(lock_id, oid):
    redis_client = redis.Redis.from_url(settings.CELERY_BROKER_URL)

    timeout_at = time.monotonic() + LOCK_EXPIRE - 3
    # Redis SETNX (set if not exists) command to acquire the lock
    acquired = redis_client.setnx(lock_id, oid)
    # Set the expiration time for the lock
    redis_client.expire(lock_id, LOCK_EXPIRE)
    try:
        yield acquired
    finally:
        # Release the lock if it was acquired and not expired
        lock_not_expired = time.monotonic() < timeout_at
        if acquired and lock_not_expired:
            redis_client.delete(lock_id)


def isolate_task(view_func):
    """This simply catches any exceptions and logs them. Used to 'isolate' periodic tasks"""

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        try:
            return view_func(*args, **kwargs)
        except Exception as exception:
            logger.exception(exception)

    return wrapped_view
