from unittest.mock import patch

import pytest

from apps.annotations.models import TagCategories
from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.channels.channels_v2.stages.core import MessageTypeValidationStage
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.channels.tests.message_examples.base_messages import text_message, unsupported_content_type_message
from apps.chat.channels import MESSAGE_TYPES


class TestMessageTypeValidationStage:
    def setup_method(self):
        self.stage = MessageTypeValidationStage()

    def test_supported_type_passes(self):
        capabilities = make_capabilities(supported_message_types=(MESSAGE_TYPES.TEXT,))
        msg = text_message()
        ctx = make_context(message=msg, capabilities=capabilities)

        # Should not raise
        self.stage(ctx)

    def test_unsupported_type_raises_early_exit(self):
        capabilities = make_capabilities(supported_message_types=(MESSAGE_TYPES.TEXT,))
        msg = unsupported_content_type_message()
        ctx = make_context(message=msg, capabilities=capabilities)

        with pytest.raises(EarlyExitResponse):
            self.stage(ctx)

    def test_unsupported_tags_human_message(self):
        capabilities = make_capabilities(supported_message_types=(MESSAGE_TYPES.TEXT,))
        msg = unsupported_content_type_message()
        ctx = make_context(message=msg, capabilities=capabilities)

        with pytest.raises(EarlyExitResponse):
            self.stage(ctx)

        assert len(ctx.human_message_tags) == 1
        tag_name, tag_category = ctx.human_message_tags[0]
        assert tag_name == "unsupported_message_type"
        assert tag_category == TagCategories.ERROR

    @patch("apps.channels.channels_v2.stages.core.MessageTypeValidationStage._generate_unsupported_response")
    def test_eventbot_failure_uses_fallback(self, mock_generate):
        mock_generate.side_effect = Exception("EventBot failed")
        capabilities = make_capabilities(supported_message_types=(MESSAGE_TYPES.TEXT,))
        msg = unsupported_content_type_message()
        ctx = make_context(message=msg, capabilities=capabilities)

        with pytest.raises(EarlyExitResponse) as exc_info:
            self.stage(ctx)

        assert "only supports" in exc_info.value.response
        assert any("Failed to generate unsupported message response" in e for e in ctx.processing_errors)
