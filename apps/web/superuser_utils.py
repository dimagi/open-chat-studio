import logging
from datetime import datetime, timedelta

from django.utils import timezone

logger = logging.getLogger("audit")

EXPIRY = 60 * 30  # 30 minutes

MAX_CONCURRENT_PRIVILEGES = 5


def apply_temporary_superuser_access(request, grant):
    if not isinstance(grant, str) or not grant.strip():
        raise ValueError("Invalid grant")

    remove_expired_temporary_superuser_access(request)
    if has_temporary_superuser_access(request, grant):
        return

    elevated_privileges = request.session.get("elevated_privileges", [])
    if len(elevated_privileges) >= MAX_CONCURRENT_PRIVILEGES:
        raise ValueError("Maximum number of concurrent privileges exceeded")

    logger.info(f"Applying temporary superuser access for '{request.user.email}' to '{grant}'")
    expire = timezone.now() + timedelta(seconds=EXPIRY)
    elevated_privileges.append((grant, int(expire.timestamp())))
    request.session["elevated_privileges"] = elevated_privileges


def has_temporary_superuser_access(request, grant):
    elevated_privileges = request.session.get("elevated_privileges", [])
    now = int(timezone.now().timestamp())
    has_access = any(granted == grant and expire > now for granted, expire in elevated_privileges)
    if not has_access:
        remove_temporary_superuser_access(request, grant)
    return has_access


def remove_temporary_superuser_access(request, grant):
    """Removes access to the specific granted and retains other valid access."""

    logger.info(f"Removing temporary superuser access for '{request.user.email}' to '{grant}'")
    remove_expired_temporary_superuser_access(request, grant)


def remove_expired_temporary_superuser_access(request, remove_grant=None):
    elevated_privileges = request.session.get("elevated_privileges", [])
    now = int(timezone.now().timestamp())
    valid = [
        (granted, expire)
        for granted, expire in elevated_privileges
        if expire > now and (remove_grant is None or granted != remove_grant)
    ]
    expired = [granted for granted, expire in elevated_privileges if expire <= now]
    if expired:
        logger.info(f"Removed expired privileges for user {request.user.email}: {','.join(expired)}")
    request.session["elevated_privileges"] = valid


def get_temporary_superuser_access(request):
    remove_expired_temporary_superuser_access(request)
    return {grant: datetime.fromtimestamp(expire) for grant, expire in request.session.get("elevated_privileges", [])}
