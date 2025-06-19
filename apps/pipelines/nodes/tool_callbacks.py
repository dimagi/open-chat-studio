class ToolCallbacks:
    def __init__(self):
        self.output_message_metadata = {}

    def attach_file(self, file_id: int):
        if "ocs_attachment_file_ids" not in self.output_message_metadata:
            self.output_message_metadata["ocs_attachment_file_ids"] = []
        self.output_message_metadata["ocs_attachment_file_ids"].append(file_id)
