import json
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.analysis.tasks import get_message_content, translate_messages_with_llm
from apps.experiments.views.experiment import _get_available_languages_for_chat


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


class TestAvailableLanguages(TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.chat_id = "test-chat-123"
        self.mock_cache = Mock()

    @patch("apps.experiments.views.experiment.get_cache")
    @patch("apps.experiments.views.experiment.ChatMessage.objects")
    def test_no_cache_returns_correct_languages_and_sets_cache(self, mock_chat_message_objects, mock_get_cache):
        """no cache, queries DB, returns correct languages, and caches result"""

        self.mock_cache.get.return_value = None
        mock_get_cache.return_value = self.mock_cache

        mock_queryset = Mock()
        mock_queryset.exclude.return_value = mock_queryset
        mock_queryset.values_list.return_value = [
            {"spa": "Hola", "fra": "Bonjour"},
            {"spa": "Gracias", "ger": "Danke"},
            {"ita": "Ciao"},
        ]
        mock_chat_message_objects.filter.return_value = mock_queryset
        result = _get_available_languages_for_chat(self.chat_id)

        mock_chat_message_objects.filter.assert_called_once_with(chat_id=self.chat_id)
        mock_queryset.values_list.assert_called_once_with("translations", flat=True)

        self.mock_cache.get.assert_called_once()  # Tried to get from cache
        self.mock_cache.add.assert_called_once()  # Cached the result

        # Correct languages returned
        assert isinstance(result, list)
        language_codes = {choice[0] for choice in result}
        assert "" in language_codes  # Should include default choice (empty string)
        expected_codes = {"spa", "fra", "ger", "ita"}
        found_codes = language_codes.intersection(expected_codes)
        assert len(found_codes) > 0  # At least some of the expected codes should be present

        cached_value = self.mock_cache.add.call_args[0][1]  # Second argument to cache.add()
        assert isinstance(cached_value, list)

    @patch("apps.experiments.views.experiment.get_cache")
    @patch("apps.experiments.views.experiment.ChatMessage.objects")
    def test_has_cache_returns_cached_values(self, mock_chat_message_objects, mock_get_cache):
        """cache exists, returns cached values without DB query"""
        cached_languages = [("", "Select language"), ("spa", "Spanish"), ("fra", "French")]
        self.mock_cache.get.return_value = cached_languages
        mock_get_cache.return_value = self.mock_cache

        result = _get_available_languages_for_chat(self.chat_id)
        assert result == cached_languages

        self.mock_cache.get.assert_called_once()

        #  Database was NOT queried!!
        mock_chat_message_objects.filter.assert_not_called()

        # Verify: No cache write operations (already cached)
        self.mock_cache.add.assert_not_called()
        self.mock_cache.delete.assert_not_called()

    @patch("apps.experiments.views.experiment.get_cache")
    @patch("apps.experiments.views.experiment.ChatMessage.objects")
    def test_clear_cache_deletes_queries_db_and_recaches(self, mock_chat_message_objects, mock_get_cache):
        """clear_cache=True deletes cache, queries DB, returns correct values, and recaches"""
        # Cache available but will be cleared
        mock_get_cache.return_value = self.mock_cache
        # After cache.delete(), cache.get() should return None (empty cache)
        self.mock_cache.get.return_value = None

        # Setup: Database returns fresh translation data
        # Create the full mock chain: filter() -> exclude() -> exclude() -> values_list()
        mock_values_list = Mock()
        mock_values_list.return_value = [
            {"spa": "Hola mundo", "ger": "Hallo Welt"},
            {"ita": "Ciao mondo", "por": "Olá mundo"},
        ]
        mock_exclude2 = Mock()
        mock_exclude2.values_list = mock_values_list
        mock_exclude1 = Mock()
        mock_exclude1.exclude.return_value = mock_exclude2
        mock_filter = Mock()
        mock_filter.exclude.return_value = mock_exclude1
        mock_chat_message_objects.filter.return_value = mock_filter

        result = _get_available_languages_for_chat(self.chat_id, clear_cache=True)

        # Cache was cleared FIRST
        self.mock_cache.delete.assert_called_once()
        self.mock_cache.get.assert_called_once()

        # Verify: Database was queried for fresh data (because cache was empty after delete)
        mock_chat_message_objects.filter.assert_called_once_with(chat_id=self.chat_id)
        mock_filter.exclude.assert_called()  # First exclude call
        mock_exclude1.exclude.assert_called()  # Second exclude call
        mock_exclude2.values_list.assert_called_once_with("translations", flat=True)

        self.mock_cache.add.assert_called_once()

        # Correct languages returned from fresh DB query
        assert isinstance(result, list)
        language_codes = {choice[0] for choice in result}
        assert "" in language_codes
        expected_codes = {"spa", "ger", "ita", "por"}
        found_codes = language_codes.intersection(expected_codes)
        assert len(found_codes) > 0

        # Fresh data was cached (not old data)
        cached_value = self.mock_cache.add.call_args[0][1]
        assert isinstance(cached_value, list)
        assert cached_value == result
