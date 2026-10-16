from unittest import mock

import pytest


@pytest.fixture()
def langfuse_session():
    session = mock.MagicMock()
    session.external_id = "ext-session-id"
    session.participant = mock.MagicMock(identifier="participant-1")
    return session
