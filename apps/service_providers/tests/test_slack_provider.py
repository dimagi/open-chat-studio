from unittest.mock import Mock

import pytest

from apps.service_providers.messaging_service import SlackService


@pytest.fixture()
def slack_service():
    service = SlackService(slack_team_id="123", slack_installation_id=1)
    mock_client = Mock(
        conversations_list=Mock(
            return_value=[
                {"channels": [{"id": "123", "name": "channel1"}]},
                {"channels": [{"id": "345", "name": "channel2"}]},
            ]
        )
    )
    service.client = mock_client
    return service


def test_iter_channels(slack_service):
    assert list(slack_service.iter_channels()) == [
        {"id": "123", "name": "channel1"},
        {"id": "345", "name": "channel2"},
    ]


def test_get_channel_by_name(slack_service):
    assert slack_service.get_channel_by_name("channel1") == {"id": "123", "name": "channel1"}
    assert slack_service.get_channel_by_name("channel2") == {"id": "345", "name": "channel2"}
    assert slack_service.get_channel_by_name("channel3") is None
