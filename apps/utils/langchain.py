from langchain.chat_models import FakeListChatModel


class FakeLlm(FakeListChatModel):
    """Extension of the FakeListChatModel that allows mocking of the token counts."""

    token_counts: list
    token_i: int = 0

    def get_num_tokens_from_messages(self, messages: list) -> int:
        token_counts = self.token_counts[self.token_i]
        if self.token_i < len(self.token_counts) - 1:
            self.token_i += 1
        else:
            self.token_i = 0
        return token_counts

    def get_num_tokens(self, text: str) -> int:
        return self.get_num_tokens_from_messages([])
