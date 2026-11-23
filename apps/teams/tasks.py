import logging
import tempfile
import zipfile
from datetime import timedelta
from uuid import uuid4

from celery import shared_task
from celery_progress.backend import ProgressRecorder
from django.core.files import File as DjangoFile
from django.utils import timezone

from apps.files.models import File, FilePurpose
from apps.teams.deletion import delete_team_if_due, get_teams_due_for_deletion
from apps.teams.invitations import send_invitation_accepted
from apps.teams.models import Invitation, Team
from apps.utils.celery import Queues

logger = logging.getLogger("ocs.teams")

CHUNK_SIZE = 64 * 1024


@shared_task(ignore_result=True, queue=Queues.BACKGROUND)
def send_invitation_accepted_notification(invitation_id):
    invitation = Invitation.objects.get(id=invitation_id)
    send_invitation_accepted(invitation)


@shared_task(queue=Queues.BACKGROUND)
def delete_teams_past_grace_period():
    """Queue the permanent deletion of every team whose deletion grace period has passed."""
    for team_id in get_teams_due_for_deletion().values_list("id", flat=True):
        delete_team_task.delay(team_id)


@shared_task(queue=Queues.BACKGROUND)
def delete_team_task(team_id):
    if delete_team_if_due(team_id):
        logger.info("Deleted team %s after its deletion grace period", team_id)


def get_team_files_queryset(team):
    """Files for the team to include in a data export, excluding export artifacts.

    ``get_all()`` bypasses the File manager's ``is_archived=False`` filter so archived files are
    included -- they're part of the export API snapshot, so their bytes must ship too. We include
    versioned copies (not just working versions) so the export is a complete snapshot, and drop
    previously generated export zips.
    """
    return File.objects.get_all().filter(team=team).exclude(purpose=FilePurpose.DATA_EXPORT).order_by("id")


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


@shared_task(bind=True, ignore_result=False, queue=Queues.BACKGROUND)
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
    files = get_team_files_queryset(team)
    total = files.count()
    export_id = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zip_file:
                seen: set[str] = set()
                for idx, file in enumerate(files.iterator(), start=1):
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
