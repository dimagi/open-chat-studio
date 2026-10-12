"""HTTP client for the source server's read endpoints. Transient transport errors (connect timeouts,
5xx, connection resets) are retried with backoff so a single network blip doesn't kill a long migration.
Rate-limited responses (429) are retried after the server's Retry-After wait. Other client errors
(4xx) fail fast. Each retry is reported through ``on_retry``."""

import logging
import time
from collections.abc import Callable

import requests

logger = logging.getLogger("ocs.teams")

_API_KEY_HEADER = "X-Api-Key"


class FileContentNotFound(Exception):
    """The source's file content API returned 404 -- the source is missing the blob too, so it can't
    be backfilled. Distinct from other HTTP errors so the importer can record it and carry on rather
    than aborting the sync."""

    def __init__(self, file_id: int) -> None:
        self.file_id = file_id
        super().__init__(f"file {file_id} has no content on the source")


class ResourceFetcher:
    def __init__(
        self,
        base_url,
        api_key,
        *,
        session=None,
        sleep=time.sleep,
        on_retry: Callable[[str], None] = logger.warning,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # Transport tuning -- fixed defaults that no caller overrides (only session/sleep are
        # injected, by tests). Promote one to an __init__ argument if a need to vary it shows up.
        # (connect, read): the read limit is long because a large export page is slow to build, but
        # finite so a dropped connection times out and is retried rather than hanging the sync.
        self.timeout = (30, 90)
        self.max_retries = 5
        self.backoff = 1.0
        self._session = session or requests.Session()
        self._sleep = sleep
        self._on_retry = on_retry
        self._team = None

    def get_manifest(self) -> dict:
        return self._get("/api/export/manifest/")

    def get_team(self) -> dict:
        """The team itself, served as a single object at the ``export/team/`` path (not a page). Cached
        for the fetcher's lifetime (one sync run): the team is read more than once per run -- the
        readiness precondition and again to anchor the import -- but doesn't change mid-sync, so a
        single request suffices."""
        if self._team is None:
            self._team = self._get("/api/export/team/")
        return self._team

    def get_page(self, resource, cursor=None, limit=100, selection=None) -> dict:
        params = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if selection is not None:
            params["selection"] = selection
        return self._get(f"/api/export/{resource}/", params)

    def iter_pages(self, resource, start_cursor=None, limit=100, selection=None):
        """Yield each page's rows as its own list. The sync writes its resume cursor once a page's
        rows are committed, so it has to see the page boundaries."""
        cursor = start_cursor
        while True:
            page = self.get_page(resource, cursor, limit, selection)
            yield page["results"]
            if not page.get("has_more"):
                return
            cursor = page["cursor"]

    def get_file_content(self, file_id: int) -> bytes:
        """The raw bytes of a file, from the source's file content API (``/api/files/<id>/content``).
        Used to backfill a synced file whose blob is missing from this server's storage. A 404 means
        the source is missing the blob too; raise ``FileContentNotFound`` so the caller can record it
        and carry on. Other client errors surface as-is."""
        try:
            return self._request(f"/api/files/{file_id}/content").content
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise FileContentNotFound(file_id) from exc
            raise

    def _get(self, path, params=None) -> dict:
        return self._request(path, params).json()

    def _request(self, path: str, params: dict | None = None) -> requests.Response:
        """GET ``path`` with the API key header, retrying transient transport errors (connect timeouts,
        5xx, connection resets) with backoff, retrying 429s after their Retry-After wait, and failing fast
        on other 4xx. Returns the raw response."""
        url = self.base_url + path
        headers = {_API_KEY_HEADER: self.api_key}
        for attempt in range(self.max_retries):
            last_attempt = attempt == self.max_retries - 1
            backoff = self.backoff * (attempt + 1)
            try:
                response = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if last_attempt:
                    raise
                self._wait_to_retry(
                    reason=f"Could not reach the source server ({type(exc).__name__})", path=path, wait=backoff
                )
                continue

            if response.status_code >= 500 and not last_attempt:
                response.close()  # return the socket to the pool before backing off
                self._wait_to_retry(reason=f"Source server returned {response.status_code}", path=path, wait=backoff)
                continue

            if response.status_code == 429 and not last_attempt:
                wait = self._retry_after(response, default=backoff)
                response.close()
                self._wait_to_retry(reason="Source server rate limit reached", path=path, wait=wait)
                continue

            response.raise_for_status()
            return response
        raise RuntimeError("request retries exhausted without a response")  # unreachable with max_retries >= 1

    def _wait_to_retry(self, reason: str, path: str, wait: float) -> None:
        self._on_retry(f"{reason}; retrying {path} in {wait:g}s")
        self._sleep(wait)

    @staticmethod
    def _retry_after(response: requests.Response, default: float) -> float:
        """The Retry-After header in seconds, or ``default`` when absent or not a number of seconds (the
        source server only sends seconds, so the HTTP-date form isn't parsed)."""
        try:
            return max(int(response.headers["Retry-After"]), 0)
        except (KeyError, ValueError):
            return default
