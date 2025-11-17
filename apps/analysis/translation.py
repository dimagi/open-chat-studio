import json

from apps.chat.models import ChatMessage
from apps.service_providers.llm_service.default_models import get_model_parameters
from apps.teams.utils import current_team

from .const import LANGUAGE_CHOICES

TRANSLATION_PROMPT_TEMPLATE = """### Instructions
Translate chat messages to {target_lang_name}. Return a JSON array where each object has the following fields:
- id: The ID of the message (must match the ID of of the input message)
- translation: The translated text
<example>
[{{"id": "4", "translation": "Hello"}}, {{"id": "9", "translation": "Hi, how can I help?"}}]
</example>
**Translation rules:**
- If the text is already in {target_lang_name}, return the original text unchanged
- Preserve the order, meaning, and tone of the original messages
### Messages to translate:
<messages>
{message_data}
</messages>
Final instructions:
Output only the JSON array with translations, without any additional text or explanation."""


class TranslationError(Exception):
    pass


def translate_messages_with_llm(messages, target_language, llm_provider, llm_provider_model):
    """
    Translate chat messages using the specified LLM provider and model
    Only translates messages that don't already have the target language translation.
    """
    messages_to_translate = []

    for msg in messages:
        if not msg.translations:
            msg.translations = {}

        if target_language not in msg.translations:
            messages_to_translate.append(msg)

    if not messages_to_translate:
        return messages
    try:
        with current_team(llm_provider.team):
            llm_service = llm_provider.get_llm_service()
            model_name = llm_provider_model.name
            params = get_model_parameters(model_name, temperature=0.1)
            llm = llm_service.get_chat_model(model_name, **params)
            message_data = []
            for msg in messages_to_translate:
                message_data.append({"id": str(msg.id), "content": msg.content, "role": msg.role})

            language_names = dict(choice for choice in LANGUAGE_CHOICES if choice[0])
            target_lang_name = language_names.get(target_language, target_language)

            prompt = TRANSLATION_PROMPT_TEMPLATE.format(
                target_lang_name=target_lang_name, message_data=json.dumps(message_data)
            )

            response = llm.invoke(prompt)
            try:
                translated_data = json.loads(response.text())
            except json.JSONDecodeError as e:
                raise TranslationError(
                    f"Failed to parse LLM response as JSON for {target_language} translation. Error: {str(e)}"
                ) from e

            messages_by_id = {str(msg.id): msg for msg in messages_to_translate}
            updated_messages = []

            for item in translated_data:
                message_id = item["id"]
                if message_id in messages_by_id:
                    message = messages_by_id[message_id]
                    message.translations[target_language] = item["translation"]
                    updated_messages.append(message)

            if updated_messages:
                ChatMessage.objects.bulk_update(updated_messages, fields=["translations"])
                chat = updated_messages[0].chat
                if target_language not in chat.translated_languages:
                    chat.translated_languages.append(target_language)
                    chat.save(update_fields=["translated_languages"])

            return messages

    except Exception as e:
        raise TranslationError(f"Failed to translate messages to {target_language}: {str(e)}") from e


def get_message_content(message, target_language=None):
    return message.translations.get(target_language, message.content)
