# Privilege Elevation

Staff and superusers do not act with their full privileges by default. To use the admin
surfaces, or to enter a team they are not a member of, they *elevate*: they prove their
identity again and receive a time-limited grant for one surface. The code lives in
`apps/web/elevation.py`.

## Grants

A grant names what the user is elevated into. Each grant has a minimum role.

| Grant | Wire form | Minimum role | Gates |
|---|---|---|---|
| `Grant.DJANGO_ADMIN` | `django_admin` | `is_staff` | `/django-admin/`, through `OcsAdminSite.admin_view` |
| `Grant.OCS_ADMIN` | `ocs_admin` | `is_staff` | every view under `/admin/`, and the team internal-metadata view |
| `Grant.team(slug)` | `team:<slug>` | `is_superuser` | `/a/<slug>/…` for a non-member, through `check_superuser_team_access` |

The wire form is what the session and URLs carry. Use `Grant.parse()` to read one; it raises
`InvalidGrant` for anything it does not recognise, and views turn that into a 404.

## Lifecycle

1. A gated view finds the grant missing and redirects to its acquire URL with `next` set to
   the current page: `/sudo/django-admin/`, `/sudo/ocs-admin/` or `/sudo/team/<slug>/`.
2. The acquire view hands the identity proof to allauth with `stash_and_reauthenticate`. The
   user answers with their password or an MFA authenticator. allauth rate limits the prompt,
   and refuses with a 403 when the user has neither method.
3. allauth calls `complete_elevation`, which rejects a stash older than `STASH_MAX_AGE`
   (120 seconds), records the grant and redirects to `next`.
4. The grant lasts 30 minutes (`EXPIRY`). A session holds at most 5 grants
   (`MAX_CONCURRENT_ELEVATIONS`).
5. The banner on every page lists the held grants, each linking to `/sudo/release/<grant>/`.

Do not gate on allauth's `did_recently_authenticate`. Its timer is shared with login and is
five minutes wide, so a user who has just logged in would be elevated without a prompt, and it
returns `True` for users with no password and no MFA.

## Gating a view

Function views use `requires_elevation`:

```python
from apps.web.elevation import Grant, requires_elevation


@requires_elevation(Grant.OCS_ADMIN)
def my_admin_view(request): ...


@requires_elevation(Grant.OCS_ADMIN, superuser_only=True)
def my_superuser_admin_view(request): ...
```

The provider-usage, provider-keys and tracing-usage APIs under `/admin/api/` use
`superuser_or_reporting_token` instead: a request carrying `PROVIDER_REPORTING_API_TOKEN` is
served without elevation, because a headless consumer cannot answer a re-authentication
prompt. Browser sessions on those URLs still need the grant.

A user below the grant's minimum role (or below superuser, with `superuser_only`) gets a 404,
not a login redirect, so the admin surfaces cannot be discovered by probing.

The Django admin site applies the gate in `OcsAdminSite.admin_view`. A custom URL on a
`ModelAdmin` must be wrapped with `self.admin_site.admin_view(...)`, as the Django docs
describe.

Team views need nothing extra: `login_and_team_required` falls back to the team grant for a
superuser who is not a member.

## Architecture guard

`apps/web/tests/test_elevation_guard.py` walks every URL under `/admin/` and `/django-admin/`
and fails if a view is not gated. It recognises views wrapped by `requires_elevation` or
`OcsAdminSite.admin_view` (both set `ENFORCES_ELEVATION_ATTR`), and Django's own admin URLs,
which call `admin_view` on each request. A view that is deliberately ungated goes in
`UNGATED_VIEW_ALLOWLIST` with a reason.

## Audit

Every grant writes a `SuperuserElevation` row (`apps/web/models.py`) with the user, grant,
expiry, IP and user agent. An explicit release stamps `released_at`; a grant that was never
released ended at `expires_at`. The rows are read-only in the Django admin. Grants, releases,
expiries and refusals are also logged to the `ocs.audit` logger.

## Development and tests

- `ELEVATION_WITHOUT_PROOF` (on by default when `DEBUG` is set, off under tests) grants team
  elevation on first access without a prompt. It does not apply to the admin grants.
- In tests, `apps.utils.tests.elevation.elevate_session(client, grant)` puts a grant in the
  test client's session. The full re-authentication round trip is covered in
  `apps/web/tests/test_elevation_views.py`.
