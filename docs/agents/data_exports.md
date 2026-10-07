# Data Exports

A user-triggered export whose size grows with team data runs as a background task that stores its output as a file. The browser polls the task and offers a download link when it finishes. Do not build these exports inside the request/response cycle.

```text
view ──starts──▶ Celery task ──writes──▶ File (DATA_EXPORT, expiring)
  │                  │                         │
  ▼                  ▼                         ▼
progress UI ◀──polls progress── task result {"file_id"} ──▶ download link
```

## View

Validates the request, starts the task with IDs only, and returns a progress UI bound to the task ID.

```python
task = export_task.delay(object_id=obj.id, team_id=request.team.id)
return TemplateResponse(request, "<app>/partials/export_progress.html", {"task_id": task.id})
```

## Task

* Runs on the background queue, scoped to the team.
* Reports progress against a known total.
* Streams rows into a temporary file, so memory stays flat regardless of export size.
* Hands that file to storage as a `File` with `purpose=FilePurpose.DATA_EXPORT` and an expiry date. Pass the temp file itself, not its contents read into memory.
* Returns the file ID on success, or a user-facing error message on failure.

```python
@shared_task(bind=True, queue=Queues.BACKGROUND)
def export_task(self, object_id: int, team_id: int) -> dict:
    try:
        ...  # load scoped to team_id, count total
        # rows: a generator that calls ProgressRecorder(self).set_progress every N rows
        with current_team(team), write_rows_to_tempfile(rows) as tmp:
            file = File.objects.create(
                team=team,
                name=filename,
                file=DjangoFile(tmp, name=filename),
                purpose=FilePurpose.DATA_EXPORT,
                expiry_date=timezone.now() + timedelta(days=7),
            )
        return {"file_id": file.id}
    except Exception:
        logger.exception("Export failed")
        return {"error": EXPORT_FAILED_MESSAGE}
```

`write_rows_to_tempfile` is a placeholder. It opens a binary `tempfile.SpooledTemporaryFile` with a positive `max_size` (the existing exports use 10 MB; the default of 0 never rolls over to disk), writes the CSV through an `io.TextIOWrapper`, then calls `flush()` and `detach()` on the wrapper (closing it would close the temp file) and `seek(0)` on the temp file before returning it.

## Progress UI

Polls the task status, shows progress, and on completion links to the stored file. Every terminal state, success or failure, hides the start control and the bar; a failure shows the error message instead of the link.

The page that hosts the progress partial loads `{% static 'celery_progress/celery_progress.js' %}` in `{% block page_head %}`. The partial is swapped in later, so it cannot load the script itself.

```javascript
CeleryProgressBar.initProgressBar("{% url 'celery_progress:task_status' task_id %}", {
  onProgress: (_bar, _msg, progress) => { /* update bar with progress.percent */ },
  onSuccess: (_bar, _msg, result) => {
    if (!result?.file_id) { /* hide start control, show result.error */ return; }
    // link to {% url 'files:base' team.slug 0 %} with 0 replaced by result.file_id, plus "?allow_s3"
  },
  onTaskError: () => { /* task crashed in the worker: hide start control, show error */ },
  onError: () => { /* network or HTTP error: hide start control, show error */ },
});
```

## Synchronous exports

Responding directly with the file is acceptable only when the output size has a small fixed upper bound.
