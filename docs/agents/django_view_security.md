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
