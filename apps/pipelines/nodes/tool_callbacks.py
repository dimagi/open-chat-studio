from apps.chat.models import ChatMessageMetadataKeys
from apps.pipelines.nodes.base import Intents


class ToolCallbacks:
    def __init__(self):
        self.output_message_metadata = {}
        self.intents = []

    def attach_file(self, file_id: int):
        key = ChatMessageMetadataKeys.OCS_ATTACHMENT_FILE_IDS
        if key not in self.output_message_metadata:
            self.output_message_metadata[key] = []
        self.output_message_metadata[key].append(file_id)

    def register_intent(self, intent: Intents):
        if intent not in self.intents:
            self.intents.append(intent)
