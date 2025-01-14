import logging
from uuid import UUID

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("connectid-api")


class CommCareConnectClient:
    def __init__(self):
        self._base_url = settings.CONNECT_ID_SERVER_BASE_URL
        self._auth = HTTPBasicAuth(settings.COMMCARE_CONNECT_SERVER_ID, settings.COMMCARE_CONNECT_SERVER_SECRET)

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type(requests.ConnectionError),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def create_channel(self, connect_id: UUID, channel_source: str) -> UUID:
        url = f"{self._base_url}/messaging/create_channel"
        response = requests.post(
            url, json={"connectid": str(connect_id), "channel_source": channel_source}, auth=self._auth, timeout=10
        )
        response.raise_for_status()
        return UUID(response.json()["channel_id"])
