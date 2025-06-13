from unittest.mock import Mock, patch

import pytest

from apps.service_providers.llm_service.index_managers import LocalIndexManager, RemoteIndexManager


@pytest.fixture()
def index_manager_mock():
    index_manager = Mock(spec=RemoteIndexManager)
    with patch("apps.service_providers.models.LlmProvider.get_remote_index_manager") as get_remote_index_manager:
        index_manager.client = Mock()
        get_remote_index_manager.return_value = index_manager
        yield index_manager


@pytest.fixture()
def local_index_manager_mock():
    index_manager = Mock(spec=LocalIndexManager)
    with patch("apps.service_providers.models.LlmProvider.get_local_index_manager") as get_local_index_manager:
        index_manager.client = Mock()
        get_local_index_manager.return_value = index_manager
        yield index_manager
