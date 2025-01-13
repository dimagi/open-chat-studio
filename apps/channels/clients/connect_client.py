from uuid import UUID

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth


class ConnectClient:
    def __init__(self):
        self._base_url = settings.CONNECT_ID_SERVER_BASE_URL
        self._auth = HTTPBasicAuth(settings.CONNECT_MESSAGING_SERVER_ID, settings.CONNECT_MESSAGING_SERVER_SECRET)

    def create_channel(self, connect_id: UUID, channel_source: str) -> UUID:
        url = f"{self._base_url}/messaging/create_channel"
        response = requests.post(
            url, json={"connectid": str(connect_id), "channel_source": channel_source}, auth=self._auth
        )
        response.raise_for_status()
        return UUID(response.json()["channel_id"])
