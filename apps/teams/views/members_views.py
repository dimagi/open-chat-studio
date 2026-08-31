from django.contrib.humanize.templatetags.humanize import naturaltime
from django_tables2 import SingleTableView

from apps.teams.backends import (
    ANNOTATION_REVIEWER_GROUP,
    ASSISTANT_ADMIN_GROUP,
    CHAT_VIEWER_GROUP,
    CHATBOT_ADMIN_GROUP,
    EVALUATION_ADMIN_GROUP,
    EVENT_ADMIN_GROUP,
    SUPER_ADMIN_GROUP,
    TEAM_ADMIN_GROUP,
)
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.tables import MembersTable

ROLE_CHOICES = [
    SUPER_ADMIN_GROUP,
    TEAM_ADMIN_GROUP,
    CHATBOT_ADMIN_GROUP,
    EVENT_ADMIN_GROUP,
    ASSISTANT_ADMIN_GROUP,
    CHAT_VIEWER_GROUP,
    EVALUATION_ADMIN_GROUP,
    ANNOTATION_REVIEWER_GROUP,
]


def _initials(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper()


def _member_row(membership) -> dict:
    groups = list(membership.groups.all())
    last_login = membership.user.last_login
    status_detail = f"Active {naturaltime(last_login)}" if last_login else "Never logged in"
    display_name = membership.user.get_display_name()
    return {
        "id": f"member-{membership.pk}",
        "kind": "member",
        "membership_pk": membership.pk,
        "name": display_name,
        "email": membership.user.email,
        "initials": _initials(display_name),
        "role_list": [group.name for group in groups],
        "status": status_detail,
        "status_label": "Active" if last_login else "Never logged in",
        "status_detail": status_detail if last_login else "",
        "status_kind": "active",
        "sort_key": membership.user.email,
    }


def _invitation_row(invitation) -> dict:
    groups = list(invitation.groups.all())
    return {
        "id": f"invite-{invitation.id}",
        "kind": "invitation",
        "invitation_id": str(invitation.id),
        "name": invitation.email,
        "email": invitation.email,
        "initials": _initials(invitation.email),
        "role_list": [group.name for group in groups],
        "status": "Invited",
        "status_label": "Invited",
        "status_detail": f"Sent {invitation.created_at:%b %-d}",
        "status_kind": "invited",
        "sort_key": invitation.email,
    }


def get_member_rows(team) -> list[dict]:
    """One row per team member plus one per pending invitation, merged and sorted by email."""
    rows = [_member_row(membership) for membership in team.sorted_memberships]
    rows += [_invitation_row(invitation) for invitation in team.pending_invitations()]
    rows.sort(key=lambda row: row["sort_key"])
    return rows


def filter_member_rows(rows: list[dict], params) -> list[dict]:
    search = params.get("search", "").strip().lower()
    role = params.get("role", "")
    status = params.get("status", "")
    if search:
        rows = [row for row in rows if search in row["name"].lower() or search in row["email"].lower()]
    if role:
        rows = [row for row in rows if role in row["role_list"]]
    if status == "active":
        rows = [row for row in rows if row["status_kind"] == "active"]
    elif status == "invited":
        rows = [row for row in rows if row["status_kind"] == "invited"]
    return rows


class MembersTableView(LoginAndTeamRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    table_class = MembersTable
    template_name = "teams/components/members_table.html"

    def get_queryset(self):
        self.all_rows = get_member_rows(self.request.team)
        return filter_member_rows(self.all_rows, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["total_count"] = len(self.all_rows)
        return context
