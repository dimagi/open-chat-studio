# Team-Based Multi-Tenancy
* All data is scoped to teams via `BaseTeamModel`
* Use `@login_and_team_required` or `@team_required` decorators on views
* Team context available in middleware as `request.team` and `request.team_membership`
* Permission system based on team membership
* Never allow cross-team data access without explicit permission
