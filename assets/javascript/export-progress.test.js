import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {buildDownloadUrl, trackExportProgress} from "./export-progress.js";

function render({restart = false} = {}) {
  document.body.innerHTML = `
    <div data-export>
      <button data-export-start ${restart ? "data-export-restart" : ""}>Start</button>
      <div id="root"
           data-progress-url="/celery-progress/abc/"
           data-download-url="/a/team/files/0/"
           data-poll-interval="10"
           data-msg-starting="Starting..."
           data-msg-retrying="Retrying..."
           data-msg-failed="Export failed."
           data-msg-network-error="Network error"
           data-msg-server-error="Server error">
        <div data-export-bar-panel>
          <div data-export-bar style="width: 0%"></div>
          <div data-export-message></div>
        </div>
        <a data-export-link style="display: none;" href="#">Download</a>
        <span data-export-error style="display: none;"><span data-export-error-message></span></span>
      </div>
    </div>`;
  return {
    root: document.getElementById("root"),
    start: document.querySelector("[data-export-start]"),
    bar: document.querySelector("[data-export-bar]"),
    message: document.querySelector("[data-export-message]"),
    link: document.querySelector("[data-export-link]"),
    error: document.querySelector("[data-export-error]"),
    errorMessage: document.querySelector("[data-export-error-message]"),
  };
}

function respond(...bodies) {
  const fetchImpl = vi.fn();
  for (const body of bodies) {
    fetchImpl.mockResolvedValueOnce({ok: true, json: async () => body});
  }
  return fetchImpl;
}

describe("buildDownloadUrl", () => {
  it.each([
    ["/a/team/files/0/", "/a/team/files/42/?allow_s3"],
    ["/a/team/files/0", "/a/team/files/42?allow_s3"],
  ])("puts the file id into %s", (url, expected) => {
    expect(buildDownloadUrl(url, 42)).toBe(expected);
  });

  it("does not treat a $ in the file id as a replacement pattern", () => {
    expect(buildDownloadUrl("/files/0/", "$1")).toBe("/files/$1/?allow_s3");
  });
});

describe("trackExportProgress", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows progress, then the download link and hides the start control", async () => {
    const ui = render();
    const fetchImpl = respond(
      {state: "PROGRESS", complete: false, progress: {percent: 40, description: "Processed 4 of 10"}},
      {state: "SUCCESS", complete: true, success: true, result: {file_id: 7}},
    );

    await trackExportProgress(ui.root, {fetchImpl});
    expect(ui.start.disabled).toBe(true);
    expect(ui.bar.style.width).toBe("40%");
    expect(ui.message.textContent).toBe("Processed 4 of 10");

    await vi.advanceTimersToNextTimerAsync();
    expect(ui.link.style.display).toBe("");
    expect(ui.link.getAttribute("href")).toBe("/a/team/files/7/?allow_s3");
    expect(ui.start.style.display).toBe("none");
  });

  it("re-enables a start control marked data-export-restart", async () => {
    const ui = render({restart: true});
    const fetchImpl = respond({state: "SUCCESS", complete: true, success: true, result: {file_id: 7}});

    await trackExportProgress(ui.root, {fetchImpl});

    expect(ui.start.disabled).toBe(false);
    expect(ui.start.style.display).toBe("");
  });

  it.each([
    ["returned error", {state: "SUCCESS", complete: true, success: true, result: {error: "No files."}}, "No files."],
    ["missing file id", {state: "SUCCESS", complete: true, success: true, result: null}, "Export failed."],
    ["task raised", {state: "FAILURE", complete: true, success: false, result: "boom"}, "Export failed."],
  ])("shows an error when the %s", async (_name, status, expected) => {
    const ui = render();

    await trackExportProgress(ui.root, {fetchImpl: respond(status)});

    expect(ui.errorMessage.textContent).toBe(expected);
    expect(ui.error.style.display).toBe("flex");
    expect(ui.link.style.display).toBe("none");
    expect(ui.start.style.display).toBe("none");
  });

  it("keeps polling while celery retries the task", async () => {
    const ui = render();
    const fetchImpl = respond(
      {state: "RETRY", complete: true, success: false, result: {next_retry_seconds: 60}},
      {state: "SUCCESS", complete: true, success: true, result: {file_id: 7}},
    );

    await trackExportProgress(ui.root, {fetchImpl});
    expect(ui.message.textContent).toBe("Retrying...");

    await vi.advanceTimersToNextTimerAsync();
    expect(ui.link.style.display).toBe("");
  });

  it.each([
    ["network", () => Promise.reject(new TypeError("offline")), "Network error"],
    ["server", () => Promise.resolve({ok: false}), "Server error"],
  ])("shows a %s error", async (_name, fetchImpl, expected) => {
    const ui = render();

    await trackExportProgress(ui.root, {fetchImpl});

    expect(ui.errorMessage.textContent).toBe(expected);
  });
});
