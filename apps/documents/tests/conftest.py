from unittest.mock import Mock, patch

import pytest

from apps.service_providers.llm_service.index_managers import OpenAIVectorStoreManager


@pytest.fixture()
def index_manager_mock():
    index_manager = Mock(spec=OpenAIVectorStoreManager)
    with patch("apps.service_providers.models.LlmProvider.get_index_manager") as get_index_manager:
        index_manager.client = Mock()
        get_index_manager.return_value = index_manager
        yield index_manager
