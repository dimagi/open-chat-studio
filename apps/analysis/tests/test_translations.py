import json
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.analysis.tasks import get_message_content, translate_messages_with_llm


class TestTranslateMessagesWithLLM(TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.mock_team = Mock()
        self.mock_team.id = 1

        self.mock_llm_provider = Mock()
        self.mock_llm_provider.team = self.mock_team

        self.mock_llm_provider_model = Mock()
        self.mock_llm_provider_model.name = "gpt-4"

        self.mock_chat = Mock()
        self.mock_chat.translated_languages = []

        self.mock_message1 = Mock()
        self.mock_message1.id = 1
        self.mock_message1.content = "Hello, how are you?"
        self.mock_message1.role = "user"
        self.mock_message1.translations = {}
        self.mock_message1.chat = self.mock_chat

        self.mock_message2 = Mock()
        self.mock_message2.id = 2
        self.mock_message2.content = "I'm fine, thank you!"
        self.mock_message2.role = "assistant"
        self.mock_message2.translations = {}
        self.mock_message2.chat = self.mock_chat

    def test_empty_messages_returns_empty_list(self):
        result = translate_messages_with_llm([], "spa", self.mock_llm_provider, self.mock_llm_provider_model)
        assert result == []

    def test_already_translated_messages_not_retranslated(self):
        self.mock_message1.translations = {"spa": "Hola, ¿cómo estás?"}
        self.mock_message2.translations = {"spa": "¡Estoy bien, gracias!"}

        messages = [self.mock_message1, self.mock_message2]

        with patch("apps.analysis.tasks.current_team"):
            result = translate_messages_with_llm(messages, "spa", self.mock_llm_provider, self.mock_llm_provider_model)

        assert result == messages
        self.mock_llm_provider.get_llm_service.assert_not_called()

    @patch("apps.analysis.tasks.current_team")
    def test_successful_translation(self, mock_current_team):
        """Test successful translation of messages"""

        mock_llm_response = Mock()
        mock_llm_response.content = json.dumps(
            [{"id": "1", "translation": "Hola, ¿cómo estás?"}, {"id": "2", "translation": "¡Estoy bien, gracias!"}]
        )

        mock_llm = Mock()
        mock_llm.invoke.return_value = mock_llm_response

        mock_llm_service = Mock()
        mock_llm_service.get_chat_model.return_value = mock_llm

        self.mock_llm_provider.get_llm_service.return_value = mock_llm_service

        messages = [self.mock_message1, self.mock_message2]
        with patch("apps.chat.models.ChatMessage.objects.bulk_update") as mock_bulk_update:
            translate_messages_with_llm(messages, "spa", self.mock_llm_provider, self.mock_llm_provider_model)
            mock_bulk_update.assert_called_once()

        assert self.mock_message1.translations["spa"] == "Hola, ¿cómo estás?"
        assert self.mock_message2.translations["spa"] == "¡Estoy bien, gracias!"

        assert "spa" in self.mock_chat.translated_languages
        self.mock_chat.save.assert_called_once_with(update_fields=["translated_languages"])

    @patch("apps.analysis.tasks.current_team")
    def test_partial_translation_existing_messages(self, mock_current_team):
        # message 1 already has translation
        self.mock_message1.translations = {"spa": "Hola, ¿cómo estás?"}

        mock_llm_response = Mock()
        mock_llm_response.content = json.dumps([{"id": "2", "translation": "¡Estoy bien, gracias!"}])
        mock_llm = Mock()
        mock_llm.invoke.return_value = mock_llm_response
        mock_llm_service = Mock()
        mock_llm_service.get_chat_model.return_value = mock_llm
        self.mock_llm_provider.get_llm_service.return_value = mock_llm_service

        messages = [self.mock_message1, self.mock_message2]

        with patch("apps.chat.models.ChatMessage.objects.bulk_update") as mock_bulk_update:
            translate_messages_with_llm(messages, "spa", self.mock_llm_provider, self.mock_llm_provider_model)
            mock_bulk_update.assert_called_once()

        assert self.mock_message1.translations["spa"] == "Hola, ¿cómo estás?"
        assert self.mock_message2.translations["spa"] == "¡Estoy bien, gracias!"


class TestGetMessageContent(TestCase):
    def setUp(self):
        self.mock_message = Mock()
        self.mock_message.content = "Hello world"
        self.mock_message.translations = {"spa": "Hola mundo", "fra": "Bonjour le monde"}

    def test_no_target_language_returns_original(self):
        result = get_message_content(self.mock_message, None)
        assert result == "Hello world"

    def test_existing_translation_returned(self):
        result = get_message_content(self.mock_message, "spa")
        assert result == "Hola mundo"

    def test_nonexistent_translation_returns_original(self):
        result = get_message_content(self.mock_message, "ger")
        assert result == "Hello world"

    def test_empty_translations_dict(self):
        mock_message = Mock()
        mock_message.content = "Hello world"
        mock_message.translations = {}
        result = get_message_content(mock_message, "spa")
        assert result == "Hello world"
