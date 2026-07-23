from unittest.mock import patch

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.team import TeamWithUsersFactory


def _team_admin_email(team):
    return next(m.user.email for m in team.membership_set.all() if m.is_team_admin())


@pytest.mark.django_db()
class TestNotifyOpenAiAssistantRemovalCommand:
    def test_no_assistants(self, capsys):
        """If no assistants exist, nothing is sent."""
        call_command("notify_openai_assistant_removal", force=True)
        assert "No OpenAI Assistants found" in capsys.readouterr().out
        assert len(mail.outbox) == 0

    def test_notifies_team_admins(self, team_with_users):
        """Admins of teams with a working assistant receive an email listing it."""
        assistant = OpenAiAssistantFactory(team=team_with_users, name="My Assistant")

        call_command("notify_openai_assistant_removal", force=True)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == [_team_admin_email(team_with_users)]
        assert team_with_users.name in email.subject
        assert assistant.name in email.body
        assert "26 August 2026" in email.body

    def test_assistant_names_not_html_escaped(self, team_with_users):
        """Plain-text email must render special characters literally, not as HTML entities."""
        OpenAiAssistantFactory(team=team_with_users, name="Bob's & Co <Bot>")

        call_command("notify_openai_assistant_removal", force=True)

        assert len(mail.outbox) == 1
        assert "Bob's & Co <Bot>" in mail.outbox[0].body

    def test_dry_run_does_not_send(self, team_with_users):
        """Dry run previews without sending email."""
        OpenAiAssistantFactory(team=team_with_users)

        call_command("notify_openai_assistant_removal", dry_run=True)

        assert len(mail.outbox) == 0

    def test_archived_and_versioned_assistants_excluded(self, team_with_users):
        """Archived assistants and published version snapshots do not trigger a notification."""
        OpenAiAssistantFactory(team=team_with_users, name="Archived Bot", is_archived=True)
        working = OpenAiAssistantFactory(team=team_with_users, name="Working Bot")
        OpenAiAssistantFactory(team=team_with_users, name="Versioned Bot", working_version=working)

        call_command("notify_openai_assistant_removal", force=True)

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert "Working Bot" in body
        assert "Archived Bot" not in body
        assert "Versioned Bot" not in body

    def test_team_ids_scopes_notification(self, team_with_users):
        """--team-ids limits notifications to the given teams, leaving others un-notified."""
        other_team = TeamWithUsersFactory()
        OpenAiAssistantFactory(team=team_with_users, name="Included Bot")
        OpenAiAssistantFactory(team=other_team, name="Excluded Bot")

        call_command("notify_openai_assistant_removal", force=True, team_ids=[team_with_users.id])

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [_team_admin_email(team_with_users)]

    @patch("apps.data_migrations.management.commands.notify_openai_assistant_removal.send_bulk_team_admin_emails")
    def test_failed_delivery_raises(self, mock_send, team_with_users):
        """A delivery failure raises so run_once does not record the migration as applied."""
        mock_send.return_value = {"sent": 0, "failed": 1, "no_admins": 0, "errors": ["boom"]}
        OpenAiAssistantFactory(team=team_with_users)

        with pytest.raises(CommandError):
            call_command("notify_openai_assistant_removal", force=True)
