"""HTTP client for the source server's read endpoints. Transient transport errors (timeouts, 5xx,
connection resets) are retried with backoff so a single network blip doesn't kill a long migration;
client errors (4xx) fail fast."""

import time

import requests

_API_KEY_HEADER = "X-Api-Key"


class FileContentNotFound(Exception):
    """The source's file content API returned 404 -- the source is missing the blob too, so it can't
    be backfilled. Distinct from other HTTP errors so the importer can record it and carry on rather
    than aborting the sync."""

    def __init__(self, file_id: int) -> None:
        self.file_id = file_id
        super().__init__(f"file {file_id} has no content on the source")


class ResourceFetcher:
    def __init__(self, base_url, api_key, *, session=None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # Transport tuning -- fixed defaults that no caller overrides (only session/sleep are
        # injected, by tests). Promote one to an __init__ argument if a need to vary it shows up.
        self.timeout = 30
        self.max_retries = 5
        self.backoff = 1.0
        self._session = session or requests.Session()
        self._sleep = sleep
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

    def get_page(self, resource, cursor=None, limit=100) -> dict:
        params = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._get(f"/api/export/{resource}/", params)

    def iter_rows(self, resource, start_cursor=None, limit=100):
        cursor = start_cursor
        while True:
            page = self.get_page(resource, cursor, limit)
            yield from page["results"]
            if not page.get("has_more"):
                break
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
        """GET ``path`` with the API key header, retrying transient transport errors (timeouts, 5xx,
        connection resets) with backoff and failing fast on 4xx. Returns the raw response."""
        url = self.base_url + path
        headers = {_API_KEY_HEADER: self.api_key}
        for attempt in range(self.max_retries):
            last_attempt = attempt == self.max_retries - 1
            try:
                response = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout):
                if last_attempt:
                    raise
                self._sleep(self.backoff * (attempt + 1))
                continue

            if response.status_code >= 500 and not last_attempt:
                response.close()  # return the socket to the pool before backing off
                self._sleep(self.backoff * (attempt + 1))
                continue

            response.raise_for_status()
            return response
        raise RuntimeError("request retries exhausted without a response")  # unreachable with max_retries >= 1
