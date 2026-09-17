import logging
from datetime import datetime, timedelta

from django.utils import timezone

logger = logging.getLogger("ocs.audit")

EXPIRY = 60 * 30  # 30 minutes

MAX_CONCURRENT_PRIVILEGES = 5


class TooManyElevatedPrivileges(Exception):
    """Raised when granting access would exceed `MAX_CONCURRENT_PRIVILEGES`."""


def apply_temporary_superuser_access(request, grant):
    if not isinstance(grant, str) or not grant.strip():
        raise ValueError("Invalid grant")

    remove_expired_temporary_superuser_access(request)
    if has_temporary_superuser_access(request, grant):
        return

    elevated_privileges = request.session.get("elevated_privileges", [])
    if len(elevated_privileges) >= MAX_CONCURRENT_PRIVILEGES:
        logger.warning(
            f"Denied temporary superuser access for '{request.user.email}' to '{grant}': "
            f"already holding {len(elevated_privileges)} (max {MAX_CONCURRENT_PRIVILEGES})"
        )
        raise TooManyElevatedPrivileges(
            f"Cannot grant '{grant}': maximum of {MAX_CONCURRENT_PRIVILEGES} concurrent privileges already held"
        )

    logger.info(f"Applying temporary superuser access for '{request.user.email}' to '{grant}'")
    expire = timezone.now() + timedelta(seconds=EXPIRY)
    elevated_privileges.append((grant, int(expire.timestamp())))
    request.session["elevated_privileges"] = elevated_privileges


def has_temporary_superuser_access(request, grant):
    elevated_privileges = request.session.get("elevated_privileges", [])
    now = int(timezone.now().timestamp())
    has_access = any(granted == grant and expire > now for granted, expire in elevated_privileges)
    if not has_access:
        remove_expired_temporary_superuser_access(request)
    return has_access


def remove_temporary_superuser_access(request, grant) -> bool:
    """Removes access to the specific grant and retains other valid access.

    Returns whether the grant was held.
    """
    if not has_temporary_superuser_access(request, grant):
        return False

    logger.info(f"Removing temporary superuser access for '{request.user.email}' to '{grant}'")
    remove_expired_temporary_superuser_access(request, grant)
    return True


def remove_expired_temporary_superuser_access(request, remove_grant=None):
    elevated_privileges = request.session.get("elevated_privileges", [])
    now = int(timezone.now().timestamp())
    # Filter the stored entries rather than rebuilding them: the session's JSON serializer
    # returns each entry as a list, so re-packing them as tuples would never compare equal
    # to what is stored and every call would write.
    retained = [entry for entry in elevated_privileges if _is_retained(entry, now, remove_grant)]
    if len(retained) == len(elevated_privileges):
        # Writing here would mark the session modified on every request, since this runs
        # from `project_meta` on every rendered page.
        return

    expired = [granted for granted, expire in elevated_privileges if expire <= now]
    if expired:
        logger.info(f"Removed expired privileges for user {request.user.email}: {','.join(expired)}")
    request.session["elevated_privileges"] = retained


def _is_retained(entry, now, remove_grant):
    granted, expire = entry
    return expire > now and granted != remove_grant


def get_temporary_superuser_access(request) -> dict[str, datetime]:
    if not hasattr(request, "user") or request.user.is_anonymous:
        return {}
    remove_expired_temporary_superuser_access(request)
    return {grant: datetime.fromtimestamp(expire) for grant, expire in request.session.get("elevated_privileges", [])}
