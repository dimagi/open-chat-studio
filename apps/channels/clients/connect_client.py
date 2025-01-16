import logging
from uuid import UUID

import httpx
import requests
from django.conf import settings
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("connectid-api")


class CommCareConnectClient:
    def __init__(self):
        if not all([settings.COMMCARE_CONNECT_SERVER_ID, settings.COMMCARE_CONNECT_SERVER_SECRET]):
            raise ValueError(
                "CONNECT_ID_SERVER_BASE_URL, COMMCARE_CONNECT_SERVER_ID, and COMMCARE_CONNECT_SERVER_SECRET must be set"
            )
        self._base_url = settings.CONNECT_ID_SERVER_BASE_URL
        self.client = httpx.Client(
            auth=httpx.BasicAuth(settings.COMMCARE_CONNECT_SERVER_ID, settings.COMMCARE_CONNECT_SERVER_SECRET),
            timeout=10,
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type(requests.ConnectionError),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def create_channel(self, connect_id: UUID, channel_source: str) -> UUID:
        url = f"{self._base_url}/messaging/create_channel"
        response = self.client.post(url, json={"connectid": str(connect_id), "channel_source": channel_source})
        response.raise_for_status()
        return UUID(response.json()["channel_id"])
