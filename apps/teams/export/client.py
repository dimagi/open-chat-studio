"""HTTP client for the source server's read endpoints. Transient transport errors (timeouts, 5xx,
connection resets) are retried with backoff so a single network blip doesn't kill a long migration;
client errors (4xx) fail fast."""

import time

import requests

_API_KEY_HEADER = "X-Api-Key"


class SourceClient:
    def __init__(self, base_url, api_key, *, timeout=30, max_retries=5, backoff=1.0, session=None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self._session = session or requests.Session()
        self._sleep = sleep

    def get_manifest(self) -> dict:
        return self._get("/api/v2/manifest/")

    def get_team(self) -> dict:
        """The team itself, served as a single object at the ``team/`` root (not a paginated page)."""
        return self._get("/api/v2/team/")

    def get_page(self, resource, cursor=None, limit=100) -> dict:
        params = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._get(f"/api/v2/team/{resource}/", params)

    def iter_rows(self, resource, start_cursor=None, limit=100):
        cursor = start_cursor
        while True:
            page = self.get_page(resource, cursor, limit)
            yield from page["results"]
            if not page.get("has_more"):
                break
            cursor = page["cursor"]

    def _get(self, path, params=None) -> dict:
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
            return response.json()
        raise RuntimeError("request retries exhausted without a response")  # unreachable with max_retries >= 1
