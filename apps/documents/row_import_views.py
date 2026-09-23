from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.documents import tasks
from apps.documents.datamodels import CollectionFileMetadata, RowImportSettings
from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.documents.row_import import (
    ROW_IMPORT_EXTENSIONS,
    RowImportError,
    oversized_row_numbers,
    parse_sheet,
    render_row,
)
from apps.files.models import File, FilePurpose
from apps.teams.decorators import login_and_team_required
from apps.web.waf import WafRule, waf_allow

ROW_IMPORT_PREVIEW_ROWS = 5


def _row_import_collection(request, pk: int) -> Collection | None:
    """Return the collection when row import applies to it, or None when it is not a local index."""
    collection = get_object_or_404(Collection, id=pk, team=request.team)
    if not collection.is_index or collection.is_remote_index:
        return None
    return collection


def _parse_uploaded_sheet(uploaded_file):
    """Parse the upload, returning (sheet, oversized_row_numbers) or raising RowImportError."""
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ROW_IMPORT_EXTENSIONS:
        raise RowImportError("Row import accepts csv or tsv files only")
    if uploaded_file.size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise RowImportError(f"The file is larger than {settings.MAX_FILE_SIZE_MB} MB")
    sheet = parse_sheet(uploaded_file.read(), filename=uploaded_file.name)
    uploaded_file.seek(0)
    return sheet, oversized_row_numbers(sheet, filename=uploaded_file.name)


@waf_allow(WafRule.SizeRestrictions_BODY)
@require_POST
@login_and_team_required
@permission_required("documents.change_collection")
def row_import_preview(request, team_slug: str, pk: int):
    if _row_import_collection(request, pk) is None:
        return HttpResponseBadRequest("Row import is available for local indexes only")
    uploaded_file = request.FILES.get("file")
    context = {"error": None, "headers": [], "sample_rows": [], "row_count": 0, "oversized_rows": []}
    if uploaded_file is None:
        context["error"] = _("Choose a csv or tsv file.")
        return render(request, "documents/partials/row_import_preview.html", context)
    try:
        sheet, oversized = _parse_uploaded_sheet(uploaded_file)
    except RowImportError as exc:
        context["error"] = str(exc)
        return render(request, "documents/partials/row_import_preview.html", context)
    context.update(
        {
            "headers": sheet.headers,
            "sample_rows": [
                render_row(uploaded_file.name, sheet.headers, row) for row in sheet.rows[:ROW_IMPORT_PREVIEW_ROWS]
            ],
            "row_count": len(sheet.rows),
            "oversized_rows": oversized,
            "max_row_tokens": settings.COLLECTION_ROW_IMPORT_MAX_ROW_TOKENS,
            "max_metadata_columns": settings.COLLECTION_ROW_IMPORT_MAX_METADATA_COLUMNS,
        }
    )
    return render(request, "documents/partials/row_import_preview.html", context)


@waf_allow(WafRule.SizeRestrictions_BODY)
@require_POST
@login_and_team_required
@permission_required("documents.change_collection")
def row_import(request, team_slug: str, pk: int):
    collection = _row_import_collection(request, pk)
    if collection is None:
        return HttpResponseBadRequest("Row import is available for local indexes only")
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        messages.error(request, _("Choose a csv or tsv file."))
        return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)

    try:
        sheet, oversized = _parse_uploaded_sheet(uploaded_file)
    except RowImportError as exc:
        messages.error(request, str(exc))
        return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)
    if oversized:
        messages.error(
            request,
            _("Rows %(rows)s are too long to embed. Shorten them and try again.")
            % {"rows": ", ".join(str(row) for row in oversized[:20])},
        )
        return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)

    metadata_columns = request.POST.getlist("metadata_columns")
    unknown = [column for column in metadata_columns if column not in sheet.headers]
    if unknown:
        messages.error(request, _("Unknown columns: %(columns)s") % {"columns": ", ".join(unknown)})
        return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)
    if len(metadata_columns) > settings.COLLECTION_ROW_IMPORT_MAX_METADATA_COLUMNS:
        messages.error(
            request,
            _("Choose at most %(max)s metadata columns.")
            % {"max": settings.COLLECTION_ROW_IMPORT_MAX_METADATA_COLUMNS},
        )
        return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)

    with transaction.atomic():
        file = File.objects.create(
            team=request.team,
            name=uploaded_file.name,
            file=uploaded_file,
            purpose=FilePurpose.COLLECTION,
        )
        collection_file = CollectionFile.objects.create(
            collection=collection,
            file=file,
            status=FileStatus.PENDING,
            metadata=CollectionFileMetadata(row_import=RowImportSettings(metadata_columns=metadata_columns)),
        )
    tasks.index_collection_files_task.delay([collection_file.id])

    messages.success(
        request, _("Importing %(count)s rows from %(name)s.") % {"count": len(sheet.rows), "name": file.name}
    )
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)
