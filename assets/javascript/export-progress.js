// Drives the progress UI rendered by templates/files/partials/export_progress.html.
//
// Polls the celery_progress status endpoint for an export task whose result is
// {"file_id": ...} or {"error": ...}. The partial's root element carries the URLs and the
// translated messages as data attributes. An enclosing [data-export] element may hold the
// [data-export-start] control: it is disabled while the task runs, then hidden, or re-enabled
// when it also has data-export-restart.

export function buildDownloadUrl(downloadUrl, fileId) {
  // downloadUrl is reversed with a file id of 0; swap in the real id.
  return downloadUrl.replace(/\/0(\/?)$/, (_match, slash) => `/${fileId}${slash}`) + "?allow_s3";
}

export function trackExportProgress(root, {fetchImpl = window.fetch.bind(window)} = {}) {
  const data = root.dataset;
  const pollInterval = Number(data.pollInterval) || 500;
  const startControl = root.closest("[data-export]")?.querySelector("[data-export-start]");
  const barPanel = root.querySelector("[data-export-bar-panel]");
  const bar = root.querySelector("[data-export-bar]");
  const message = root.querySelector("[data-export-message]");
  const link = root.querySelector("[data-export-link]");
  const errorPanel = root.querySelector("[data-export-error]");

  if (startControl) startControl.disabled = true;

  function finish() {
    barPanel.style.display = "none";
    if (!startControl) return;
    if (startControl.hasAttribute("data-export-restart")) {
      startControl.disabled = false;
    } else {
      startControl.style.display = "none";
    }
  }

  function succeed(fileId) {
    finish();
    link.href = buildDownloadUrl(data.downloadUrl, fileId);
    link.style.display = "";
  }

  function fail(text) {
    finish();
    errorPanel.querySelector("[data-export-error-message]").textContent = text;
    errorPanel.style.display = "flex";
  }

  function showProgress(progress, text) {
    bar.style.width = `${progress?.percent || 0}%`;
    message.textContent = text;
  }

  function handle(status) {
    if (status.state === "RETRY") {
      showProgress(null, data.msgRetrying);
      return true;
    }
    if (!status.complete) {
      showProgress(status.progress, status.progress?.description || data.msgStarting);
      return true;
    }
    const result = status.result;
    if (status.success && result?.file_id) {
      succeed(result.file_id);
    } else if (status.success && result?.error) {
      fail(result.error);
    } else {
      fail(data.msgFailed);
    }
    return false;
  }

  async function poll() {
    let response;
    try {
      response = await fetchImpl(data.progressUrl, {headers: {Accept: "application/json"}});
    } catch {
      fail(data.msgNetworkError);
      return;
    }
    if (!response.ok) {
      fail(data.msgServerError);
      return;
    }
    if (handle(await response.json())) {
      setTimeout(poll, pollInterval);
    }
  }

  return poll();
}
