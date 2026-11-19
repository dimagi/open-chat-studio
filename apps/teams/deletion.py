"""Team deletion: a request disables the team at once, and the data is removed after a grace period.

The grace period keeps the team's records available to the cross-team reporting APIs until the
billing period they fall in has been reported, and lets an admin cancel a mistaken request.
"""

import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import openai
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.transaction import audit_transaction
from apps.documents.models import Collection
from apps.evaluations.models import EvaluationMessage
from apps.events.models import EventAction
from apps.service_providers.llm_service.index_managers import OpenAIRemoteIndexManager
from apps.teams.models import Membership, Team
from apps.teams.utils import current_team
from apps.trace.models import Trace
from apps.users.models import CustomUser
from apps.utils.deletion import (
    chunk_list,
    delete_object_with_auditing_of_related_objects,
    get_admin_emails_with_delete_permission,
    send_team_deleted_notification,
    send_team_deletion_scheduled_notification,
)

logger = logging.getLogger("ocs.teams")

NOTIFY_RECIPIENTS = ("self", "admins", "all")
EMAIL_CHUNK_SIZE = 50


class TeamDeletionError(Exception):
    pass


def request_team_deletion(team: Team, user: CustomUser, notify_recipients: str = "self") -> Team:
    """Disable the team and schedule it for permanent deletion after the grace period."""
    if notify_recipients not in NOTIFY_RECIPIENTS:
        raise TeamDeletionError(f"Unknown notification recipients: {notify_recipients}")
    if team.is_pending_deletion:
        raise TeamDeletionError("This team is already scheduled for deletion.")

    team.deletion_requested_at = timezone.now()
    team.deletion_requested_by = user
    team.deletion_notify_recipients = notify_recipients
    team.save(update_fields=["deletion_requested_at", "deletion_requested_by", "deletion_notify_recipients"])

    emails = _notification_emails(team, notify_recipients, user.email)
    transaction.on_commit(lambda: _send_scheduled_notifications(team, emails))
    return team


def cancel_team_deletion(team: Team) -> Team:
    """Restore a team that is scheduled for deletion."""
    team.deletion_requested_at = None
    team.deletion_requested_by = None
    team.deletion_notify_recipients = ""
    team.save(update_fields=["deletion_requested_at", "deletion_requested_by", "deletion_notify_recipients"])
    return team


def deletion_cutoff(now: datetime | None = None) -> datetime:
    """Teams whose deletion was requested at or before this time are due for deletion."""
    return (now or timezone.now()) - settings.TEAM_DELETION_GRACE_PERIOD


def get_teams_due_for_deletion():
    return Team.objects.filter(deletion_requested_at__lte=deletion_cutoff())


def delete_team_if_due(team_id: int) -> bool:
    """Permanently delete the team if its grace period has passed and the request still stands.

    Returns whether the team was deleted.
    """
    with transaction.atomic():
        team = (
            Team.objects.select_for_update(of=("self",))
            .select_related("deletion_requested_by")
            .filter(id=team_id, deletion_requested_at__lte=deletion_cutoff())
            .first()
        )
        if team is None:
            return False

        team_name = team.name
        requester_email = team.deletion_requested_by.email if team.deletion_requested_by else None
        emails = _notification_emails(team, team.deletion_notify_recipients, requester_email)
        remote_cleanups = _collect_remote_index_cleanups(team)

        hard_delete_team(team)

        transaction.on_commit(lambda: _run_remote_cleanups(remote_cleanups))
        transaction.on_commit(lambda: _send_deleted_notifications(team_name, emails))
    return True


def hard_delete_team(team: Team) -> dict:
    """Delete the team and every record that belongs to it.

    Most team data cascades from the team row. The models deleted explicitly here are the ones the
    cascade misses: traces (``Trace.team`` is ``SET_NULL``), evaluation messages (linked to datasets
    and runs only through many-to-many tables) and event actions (referenced by triggers rather than
    referencing them).
    """
    with transaction.atomic(), current_team(team), audit_transaction(uuid.uuid4().hex):
        Trace.objects.filter(team=team).delete()
        evaluation_messages = EvaluationMessage.objects.filter(
            Q(evaluationdataset__team=team)
            | Q(scoping_runs__team=team)
            | Q(evaluationresult__team=team)
            | Q(session__team=team)
        )
        EvaluationMessage.objects.filter(pk__in=evaluation_messages.values("pk")).delete()
        event_actions = EventAction.objects.filter(
            Q(static_trigger__experiment__team=team)
            | Q(timeout_trigger__experiment__team=team)
            | Q(scheduled_trigger__experiment__team=team)
            | Q(scheduled_messages__team=team)
        )
        EventAction.objects.filter(pk__in=event_actions.values("pk")).delete()
        return delete_object_with_auditing_of_related_objects(team)


@dataclass
class _RemoteIndexCleanup:
    manager: OpenAIRemoteIndexManager
    remote_file_ids: list[str]


def _collect_remote_index_cleanups(team: Team) -> list[_RemoteIndexCleanup]:
    """Build the clients for the team's remote vector stores while its provider credentials still exist."""
    cleanups = []
    collections = (
        Collection.objects.get_all()
        .filter(team=team, is_index=True, is_remote_index=True, llm_provider__isnull=False)
        .exclude(openai_vector_store_id="")
        .select_related("llm_provider")
    )
    for collection in collections:
        try:
            manager = collection.get_index_manager()
        except Exception:
            logger.exception("Unable to build remote index client for collection %s", collection.id)
            continue
        if not isinstance(manager, OpenAIRemoteIndexManager):
            continue
        remote_file_ids = list(collection.files.get_all().exclude(external_id="").values_list("external_id", flat=True))
        cleanups.append(_RemoteIndexCleanup(manager=manager, remote_file_ids=remote_file_ids))
    return cleanups


def _run_remote_cleanups(cleanups: list[_RemoteIndexCleanup]):
    """Delete the remote vector stores and files. Runs after the team's rows are gone, so it touches no rows."""
    for cleanup in cleanups:
        for file_id in cleanup.remote_file_ids:
            try:
                with contextlib.suppress(openai.NotFoundError):
                    cleanup.manager.client.files.delete(file_id)
            except Exception:
                logger.exception("Failed to delete remote file %s", file_id)
        try:
            cleanup.manager.delete_remote_index()
        except Exception:
            logger.exception("Failed to delete remote index %s", cleanup.manager.index_id)


def _notification_emails(team: Team, notify_recipients: str, requester_email: str | None) -> list[str]:
    if notify_recipients == "admins":
        return get_admin_emails_with_delete_permission(team)
    if notify_recipients == "all":
        return list(Membership.objects.filter(team=team).values_list("user__email", flat=True))
    return [requester_email] if requester_email else []


def _send_scheduled_notifications(team: Team, emails: list[str]):
    """Runs after the request has committed, so a mail failure is logged rather than raised."""
    for chunk in chunk_list(emails, EMAIL_CHUNK_SIZE):
        try:
            send_team_deletion_scheduled_notification(team, chunk)
        except Exception:
            logger.exception("Failed to send the deletion-scheduled email for team %s", team.id)


def _send_deleted_notifications(team_name: str, emails: list[str]):
    """Runs after the team is gone, so a mail failure is logged rather than raised."""
    for chunk in chunk_list(emails, EMAIL_CHUNK_SIZE):
        try:
            send_team_deleted_notification(team_name, chunk)
        except Exception:
            logger.exception("Failed to send the team-deleted email for team %s", team_name)
