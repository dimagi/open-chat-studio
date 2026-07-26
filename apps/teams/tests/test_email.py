import pytest
from django.core import mail

from apps.teams.backends import get_team_owner_groups
from apps.teams.email import MAX_RECIPIENTS_PER_EMAIL, send_bulk_team_admin_emails
from apps.utils.factories.team import MembershipFactory, TeamFactory


def _add_admins(team, count):
    for _ in range(count):
        MembershipFactory(team=team, groups=get_team_owner_groups)


@pytest.mark.django_db()
class TestSendBulkTeamAdminEmails:
    def test_large_admin_list_split_into_chunks(self):
        """A team with more admins than the SES limit is emailed in chunks of <= the limit."""
        team = TeamFactory()
        admin_count = MAX_RECIPIENTS_PER_EMAIL + 5
        _add_admins(team, admin_count)

        results = send_bulk_team_admin_emails(
            teams_context={team.id: {}},
            subject_template="Subject for {{ team.name }}",
            body_template_path="events/email/openai_assistant_removal.txt",
        )

        assert results["sent"] == 1
        assert results["failed"] == 0
        assert len(mail.outbox) == 2
        assert all(len(email.to) <= MAX_RECIPIENTS_PER_EMAIL for email in mail.outbox)
        recipients = [addr for email in mail.outbox for addr in email.to]
        assert len(recipients) == admin_count
        assert len(set(recipients)) == admin_count

    def test_small_admin_list_single_email(self):
        """A team within the recipient limit is emailed once with all admins."""
        team = TeamFactory()
        _add_admins(team, 3)

        results = send_bulk_team_admin_emails(
            teams_context={team.id: {}},
            subject_template="Subject for {{ team.name }}",
            body_template_path="events/email/openai_assistant_removal.txt",
        )

        assert results["sent"] == 1
        assert len(mail.outbox) == 1
        assert len(mail.outbox[0].to) == 3
