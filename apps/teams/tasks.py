import logging
import tempfile
import zipfile
from datetime import timedelta
from uuid import uuid4

from celery import shared_task
from celery_progress.backend import ProgressRecorder
from django.core.files import File as DjangoFile
from django.utils import timezone
from taskbadger.celery import Task as TaskbadgerTask

from apps.files.models import File, FilePurpose
from apps.teams.invitations import send_invitation_accepted
from apps.teams.models import Invitation, Membership, Team
from apps.teams.utils import current_team
from apps.utils.deletion import (
    chunk_list,
    delete_object_with_auditing_of_related_objects,
    get_admin_emails_with_delete_permission,
    send_team_deleted_notification,
)

logger = logging.getLogger("ocs.teams")

CHUNK_SIZE = 64 * 1024


@shared_task(ignore_result=True)
def send_invitation_accepted_notification(invitation_id):
    invitation = Invitation.objects.get(id=invitation_id)
    send_invitation_accepted(invitation)


@shared_task
def delete_team_async(team_id, user_email, notify_recipients="self"):
    team = Team.objects.get(id=team_id)
    emails = [user_email]  # default case: user sends email just to themselves
    if notify_recipients == "admins":
        emails = get_admin_emails_with_delete_permission(team)
    elif notify_recipients == "all":
        emails = list(Membership.objects.filter(team__name=team.name).values_list("user__email", flat=True))
    team_name = team.name
    chunk_size = 50
    chunked_emails = chunk_list(emails, chunk_size)
    with current_team(team):
        delete_object_with_auditing_of_related_objects(team)
        for chunk_emails in chunked_emails:
            send_team_deleted_notification(team_name, chunk_emails)


def get_team_files_queryset(team):
    """Current working files for the team, excluding export artifacts.

    The File manager already excludes archived files; we additionally limit to
    working versions and drop previously generated export zips.
    """
    return (
        File.objects.filter(team=team, working_version__isnull=True)
        .exclude(purpose=FilePurpose.DATA_EXPORT)
        .order_by("id")
    )


def _export_arcname(file: File) -> str:
    """Zip entry name mirroring the file's storage path.

    Preserving the storage layout lets the archive be re-imported into a storage
    backend with an identical layout. Relative storage locations (e.g. an S3 key
    prefix) are prepended; absolute local paths are left as the bare name.
    """
    name = file.file.name
    location = getattr(file.file.storage, "location", "") or ""
    if location and not location.startswith("/"):
        return location.rstrip("/") + "/" + name
    return name


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=False)
def create_team_files_zip_task(self, team_id: int) -> int:
    """Build a zip of the team's current files and store it as a DATA_EXPORT file.

    Returns the id of the created File so the caller can serve it later (via a
    pre-signed URL). Files with no stored content, missing from storage, or that
    fail to read are skipped rather than aborting the whole export. Regardless of
    outcome, clears the in-progress export marker (see
    Team.mark_files_export_finished) so a new export can be started.
    """
    progress_recorder = ProgressRecorder(self)
    team = Team.objects.get(id=team_id)
    files = list(get_team_files_queryset(team))
    total = len(files)
    export_id = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zip_file:
                seen: set[str] = set()
                for idx, file in enumerate(files, start=1):
                    progress_recorder.set_progress(idx, total, description=f"Adding {file.name}")
                    if not file.file:
                        continue
                    arcname = _export_arcname(file)
                    if arcname in seen:
                        logger.warning("Skipping duplicate export path %s (file id=%s)", arcname, file.id)
                        continue
                    if not file.file.storage.exists(file.file.name):
                        logger.warning("Skipping missing file %s (id=%s) from export", arcname, file.id)
                        continue
                    try:
                        with file.file.open("rb") as src, zip_file.open(arcname, "w") as dest:
                            while chunk := src.read(CHUNK_SIZE):
                                dest.write(chunk)
                    except Exception:
                        logger.exception("Skipping file id=%s while adding to export", file.id)
                        continue
                    seen.add(arcname)

            tmp.seek(0)
            filename = f"team-{team.slug}-files-{timezone.now().date().isoformat()}.zip"
            export = File.objects.create(
                team=team,
                name=filename,
                file=DjangoFile(tmp, name=filename),
                content_type="application/zip",
                expiry_date=timezone.now() + timedelta(hours=24),
                purpose=FilePurpose.DATA_EXPORT,
            )
            export_id = export.id
    finally:
        team.mark_files_export_finished(export_id)

    return export_id


def start_team_files_export(team: Team) -> str:
    """Dispatch the async team-files export, reusing an in-flight one if present.

    Returns the task id to track: the existing `team.files_export_task_id` when
    an export is already in flight, otherwise a freshly dispatched one.
    """
    if team.files_export_in_progress:
        return team.files_export_task_id
    task_id = str(uuid4())
    team.mark_files_export_started(task_id)
    try:
        create_team_files_zip_task.apply_async(args=[team.id], task_id=task_id)
    except Exception:
        team.mark_files_export_finished()
        raise
    return task_id
