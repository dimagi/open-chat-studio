from datetime import timedelta

from django.utils import timezone

EXPIRY = 300


def apply_temporary_superuser_access(request, team_slug):
    remove_expired_temporary_superuser_access(request)
    if has_temporary_superuser_access(request, team_slug):
        return

    elevated_privileges = request.session.get("elevated_privileges", [])
    expire = timezone.now() + timedelta(seconds=EXPIRY)
    elevated_privileges.append((team_slug, int(expire.timestamp())))
    request.session["elevated_privileges"] = elevated_privileges


def has_temporary_superuser_access(request, team_slug):
    elevated_privileges = request.session.get("elevated_privileges", [])
    now = int(timezone.now().timestamp())
    has_access = any(team == team_slug and expire > now for team, expire in elevated_privileges)
    if not has_access:
        remove_temporary_superuser_access(request, team_slug)
    return has_access


def remove_temporary_superuser_access(request, team_slug):
    """This removes access to the specific team and also expired access."""

    elevated_privileges = request.session.get("elevated_privileges", [])
    now = int(timezone.now().timestamp())
    request.session["elevated_privileges"] = [
        (team, expire) for team, expire in elevated_privileges if team != team_slug or expire <= now
    ]


def remove_expired_temporary_superuser_access(request):
    elevated_privileges = request.session.get("elevated_privileges", [])
    now = int(timezone.now().timestamp())
    request.session["elevated_privileges"] = [(team, expire) for team, expire in elevated_privileges if expire > now]


def get_temporary_superuser_access(request):
    remove_expired_temporary_superuser_access(request)
    return [access[0] for access in request.session.get("elevated_privileges", [])]
