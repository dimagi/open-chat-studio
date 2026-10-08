# Data Exports

A user-triggered export whose size grows with team data runs as a background task that stores its output as a file. The browser polls the task and offers a download link when it finishes. Do not build these exports inside the request/response cycle.

```text
view ──starts──▶ Celery task ──save_data_export──▶ File (DATA_EXPORT, expiring)
  │                  │                                   │
  ▼                  ▼                                   ▼
export_progress.html ◀──polls── {"file_id"} | {"error"} ──▶ download link
```

The shared pieces:

| Piece | Location |
|---|---|
| `save_data_export`, `csv_to_tempfile`, `report_progress`, `EXPORT_FAILED_MESSAGE` | `apps/files/exports.py` |
| Progress partial | `templates/files/partials/export_progress.html` |
| Polling script, exposed as `SiteJS.app.trackExportProgress` | `assets/javascript/export-progress.js` |

## View

Validates the request, starts the task with IDs only, and renders the progress partial.

```python
task = export_task.delay(object_id=obj.id, team_id=request.team.id)
return TemplateResponse(
    request, "files/partials/export_progress.html", {"task_id": task.id, "link_label": _("Download CSV")}
)
```

The partial also takes `title`, which renders it as a full-width panel with a heading instead of an inline bar.

The partial calls `SiteJS.app.trackExportProgress`, so the hosting page must load `js/app-bundle.js`. Pages extending `web/app/app_base.html` do, unless they override `{% block page_head %}` without `{{ block.super }}`.

## Task

* Runs on the background queue, scoped to the team.
* Reports progress against a known total: wrap the row iterator in `report_progress`, which updates the recorder about once per percent.
* Writes into a temporary file, so memory stays flat regardless of export size. `csv_to_tempfile` does this for CSV.
* Stores the file with `save_data_export`, passing the open temp file rather than its contents.
* Returns `{"file_id": ...}` on success, or `{"error": <user-facing message>}` on failure.

```python
@shared_task(bind=True, queue=Queues.BACKGROUND)
def export_task(self, object_id: int, team_id: int) -> dict:
    try:
        ...  # load scoped to team_id, count total
        rows = report_progress(items=rows, total=total, recorder=ProgressRecorder(self))
        with current_team(team), csv_to_tempfile(lambda writer: writer.writerows(rows)) as tmp:
            file = save_data_export(
                team=team, name=filename, file=tmp, content_type="text/csv", expires_in=timedelta(days=7)
            )
        return {"file_id": file.id}
    except Exception:
        logger.exception("Export failed")
        return {"error": EXPORT_FAILED_MESSAGE}
```

A task that relies on Celery retries may raise instead of returning `{"error"}`. The progress UI shows "Retrying..." while Celery retries and a generic failure message once it gives up.

## Start control

To have the button that started the export disabled while the task runs and hidden when it ends, put the partial inside an element with `data-export` that also holds the button, marked `data-export-start`. Add `data-export-restart` to the button to re-enable it instead of hiding it, for exports the user may want to run again with different input. See `templates/evaluations/partials/export_button.html` and `templates/experiments/components/exports.html`.

## Permissions

The download link goes to `files:base`, which requires `files.view_file`. The permission that starts the export does not grant it, so every group allowed to start the export must also have `files.view_file`, or its users get a 403 on the link.

## Synchronous exports

Responding directly with the file is acceptable only when the output size has a small fixed upper bound.
