from pydantic import BaseModel, ConfigDict, Field

from apps.files.models import File


class LlmChatResponse(BaseModel):
    """
    A data model representing a response from a large language model (LLM) chat.
    It includes the text of the response, and sets for cited and generated files.
    This model is used to structure the output from LLMs, allowing for easy access
    to the response text and any associated files.

    This model is additive, meaning that two instances can be combined
    to create a new instance that contains the combined text and files.
    This is useful for aggregating responses from multiple LLM calls or messages.
    """

    text: str
    cited_files: set[File] = Field(default_factory=set)
    generated_files: set[File] = Field(default_factory=set)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __add__(self, other: "LlmChatResponse") -> "LlmChatResponse":
        """Add two LlmChatResponse instances together by combining their fields."""
        if not isinstance(other, LlmChatResponse):
            return Exception("Cannot add LlmChatResponse with non-LlmChatResponse type.")

        return LlmChatResponse(
            text="".join([self.text, other.text]),
            cited_files=self.cited_files | other.cited_files,
            generated_files=self.generated_files | other.generated_files,
        )
