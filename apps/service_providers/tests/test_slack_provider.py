def test_iter_channels(slack_service):
    assert list(slack_service.iter_channels()) == [
        {"id": "123", "name": "channel1"},
        {"id": "345", "name": "channel2"},
    ]


def test_get_channel_by_name(slack_service):
    assert slack_service.get_channel_by_name("channel1") == {"id": "123", "name": "channel1"}
    assert slack_service.get_channel_by_name("channel2") == {"id": "345", "name": "channel2"}
    assert slack_service.get_channel_by_name("channel3") is None
