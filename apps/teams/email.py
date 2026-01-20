from django.conf import settings
from django.core.mail import send_mail
from django.template import Context, Template
from django.template.loader import render_to_string


def send_bulk_team_admin_emails(
    teams_context: dict[int, dict], subject_template: str, body_template_path: str, fail_silently=False
):
    """
    Send emails to admins of multiple teams with team-specific context.

    Args:
        teams_context: Dict mapping team_id to template context for that team
                      e.g., {1: {"experiments": ["Bot A"]}, 2: {"experiments": ["Bot B", "Bot C"]}}
        subject_template: Django template string for subject (can use team variables)
                         e.g., "Update for {{ team.name }}"
        body_template_path: Template path (e.g., "events/email/my_template.txt")
        fail_silently: Whether to suppress email errors

    Returns:
        Dict with results: {"sent": int, "failed": int, "no_admins": int, "errors": list}

    Example:
        results = send_bulk_team_admin_emails(
            teams_context={
                1: {"experiments": ["Bot A", "Bot B"]},
                2: {"experiments": ["Bot C"]},
            },
            subject_template="Open Chat Studio: Update for {{ team.name }}",
            body_template_path="events/email/my_notification.txt",
            fail_silently=False
        )
    """
    from apps.teams.models import Team

    results = {"sent": 0, "failed": 0, "no_admins": 0, "errors": []}

    # Load all teams at once
    team_ids = list(teams_context.keys())
    teams = {team.id: team for team in Team.objects.filter(id__in=team_ids)}

    for team_id, context in teams_context.items():
        team = teams.get(team_id)
        if not team:
            results["errors"].append(f"Team {team_id} not found")
            results["failed"] += 1
            continue

        # Get admin emails
        admin_emails = collect_team_admin_emails(team)
        if not admin_emails:
            results["no_admins"] += 1
            continue

        # Build email context
        email_context = {"team": team, "team_name": team.name}
        email_context.update(context)

        try:
            # Render subject from template string
            subject = Template(subject_template).render(Context(email_context))

            # Send email
            send_mail(
                subject=subject,
                message=render_to_string(body_template_path, context=email_context),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=admin_emails,
                fail_silently=fail_silently,
            )
            results["sent"] += 1

        except Exception as e:
            results["errors"].append(f"Team {team.name} (ID {team_id}): {e}")
            results["failed"] += 1

    return results


def collect_team_admin_emails(team):
    """
    Get list of all admin email addresses for a team.

    Args:
        team: Team model instance

    Returns:
        List of email addresses (strings)
    """
    admin_emails = []
    for membership in team.membership_set.select_related("user").prefetch_related("groups"):
        if membership.is_team_admin():
            admin_emails.append(membership.user.email)
    return admin_emails
