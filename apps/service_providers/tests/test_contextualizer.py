from unittest import mock

from apps.service_providers.llm_service.contextualizer import (
    LLMContextualizer,
    StaticContextualizer,
)


class TestStaticContextualizer:
    def test_uses_file_name_and_page(self):
        contextualizer = StaticContextualizer(file_name="report.pdf", page_number=3)
        context = contextualizer.get_context(document="full doc", chunk="a chunk")
        assert "report.pdf" in context
        assert "Page 3" in context

    def test_empty_when_no_metadata(self):
        contextualizer = StaticContextualizer()
        assert contextualizer.get_context(document="full doc", chunk="a chunk") == ""

    def test_ignores_page_zero(self):
        contextualizer = StaticContextualizer(file_name="report.pdf", page_number=0)
        context = contextualizer.get_context(document="d", chunk="c")
        assert "report.pdf" in context
        assert "Page" not in context


class TestLLMContextualizer:
    def test_returns_stripped_llm_output(self):
        chat_model = mock.Mock()
        chat_model.invoke.return_value = mock.Mock(text="  This chunk covers Q2 revenue.  ")
        contextualizer = LLMContextualizer(chat_model)

        context = contextualizer.get_context(document="full doc", chunk="a chunk")

        assert context == "This chunk covers Q2 revenue."
        chat_model.invoke.assert_called_once()

    def test_document_and_chunk_in_prompt(self):
        chat_model = mock.Mock()
        chat_model.invoke.return_value = mock.Mock(text="ctx")
        contextualizer = LLMContextualizer(chat_model)

        contextualizer.get_context(document="UNIQUE_DOC_MARKER", chunk="UNIQUE_CHUNK_MARKER")

        sent_messages = chat_model.invoke.call_args.args[0]
        system_message = sent_messages[0][1]
        human_message = sent_messages[-1][1]
        # Document goes in the system prompt (for prompt caching); chunk in the human message.
        assert "UNIQUE_DOC_MARKER" in system_message
        assert "UNIQUE_CHUNK_MARKER" in human_message

    def test_document_is_truncated(self):
        chat_model = mock.Mock()
        chat_model.invoke.return_value = mock.Mock(text="ctx")
        contextualizer = LLMContextualizer(chat_model, max_document_chars=10)

        contextualizer.get_context(document="X" * 5000, chunk="a chunk")

        sent_messages = chat_model.invoke.call_args.args[0]
        system_message = sent_messages[0][1]
        assert system_message.count("X") == 10

    def test_falls_back_to_static_on_llm_error(self):
        chat_model = mock.Mock()
        chat_model.invoke.side_effect = RuntimeError("provider down")
        fallback = StaticContextualizer(file_name="report.pdf")
        contextualizer = LLMContextualizer(chat_model, fallback=fallback)

        context = contextualizer.get_context(document="full doc", chunk="a chunk")

        assert "report.pdf" in context

    def test_fallback_can_be_empty(self):
        chat_model = mock.Mock()
        chat_model.invoke.side_effect = RuntimeError("provider down")
        contextualizer = LLMContextualizer(chat_model)

        assert contextualizer.get_context(document="full doc", chunk="a chunk") == ""