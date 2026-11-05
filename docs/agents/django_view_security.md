# Django View Security Patterns

Always use team-based security:
```python
from apps.teams.decorators import login_and_team_required
from django.contrib.auth.decorators import permission_required


# Function-based views
@login_and_team_required
@permission_required("my_app.view_mymodel")
def my_view(request, team_slug: str):
    current_user = request.user
    current_team = request.team
    team_membership = request.team_membership
    pass


# Class-based views
from apps.teams.mixins import LoginAndTeamRequiredMixin
from django.contrib.auth.mixins import PermissionRequiredMixin


class MyView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "my_app.view_mymodel"
```

Views for staff or superusers (anything under `/admin/` or on the Django admin site) must be gated with `requires_elevation` from `apps/web/elevation.py`, not a bare `is_staff` / `is_superuser` check. `apps/web/tests/test_elevation_guard.py` fails on an ungated admin view. See `docs/developer_guides/code_systems/privilege_elevation.md`.

## API views (DRF)

Permission classes for API views live in `apps/api/permissions.py`. Setting `permission_classes` replaces the defaults from `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]`, so always start from `BASE_PERMISSION_CLASSES`. It keeps the read-only API key gate (ADR-0021). Then add one model permission class and one OAuth scope class:

```python
from apps.api.permissions import BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView
from apps.oauth.permissions import TokenHasOAuthResourceScope


class MyModelView(GenericAPIView):
    permission_classes = [*BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    # The required permission is derived from this queryset's model, e.g. POST -> "my_app.add_mymodel".
    queryset = MyModel.objects.none()
```

- `DjangoModelPermissionsWithView` maps the HTTP method to the model permission: GET → `view`, POST → `add`, PUT/PATCH → `change`, DELETE → `delete`. Unlike DRF's `DjangoModelPermissions`, it also requires `view` for GET. Set `queryset` to the model being authorized, even if the view fetches objects some other way.
- `RequiresTeamPermission`: subclass it and set `required_permissions` when the method-to-permission mapping is wrong for the endpoint. For example, deleting a node from a chatbot's pipeline is a change to the chatbot, not a delete. Also use it when the view's queryset is not the thing being authorized.
- `TokenHasOAuthScope` / `TokenHasOAuthResourceScope` (`apps/oauth/permissions.py`) check `required_scopes` for OAuth tokens and allow other token types through.

Client-credentials (machine) tokens have no user, so the model permission classes let them through, and the OAuth scope check and the token's pinned team authorize them.
