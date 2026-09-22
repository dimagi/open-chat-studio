"""The lorem ipsum text the mock answers with."""

import random

SENTENCE_COUNTS = {"short": 1, "medium": 10, "long": 30}
SENTENCES_PER_PARAGRAPH = 5
CHARS_PER_TOKEN = 4

_LOREM_TEXT = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et "
    "dolore magna aliqua enim ad minim veniam quis nostrud exercitation ullamco laboris nisi aliquip ex ea "
    "commodo consequat duis aute irure in reprehenderit voluptate velit esse cillum eu fugiat nulla pariatur "
    "excepteur sint occaecat cupidatat non proident sunt culpa qui officia deserunt mollit anim id est laborum"
)
_LOREM_WORDS = _LOREM_TEXT.split()


def _sentence(index: int) -> str:
    """A lorem sentence that is the same every time for a given index."""
    rng = random.Random(index)
    words = [rng.choice(_LOREM_WORDS) for _ in range(rng.randint(8, 18))]
    return f"{words[0].capitalize()} {' '.join(words[1:])}."


def build_response_text(length: str, index: int = 0) -> str:
    """Lorem ipsum of the requested size. `index` rotates the wording between calls."""
    total = SENTENCE_COUNTS[length]
    sentences = [_sentence(index * total + offset) for offset in range(total)]
    paragraphs = [
        " ".join(sentences[start : start + SENTENCES_PER_PARAGRAPH])
        for start in range(0, total, SENTENCES_PER_PARAGRAPH)
    ]
    return "\n\n".join(paragraphs)


def count_tokens(text: str) -> int:
    """A rough token count, so OCS's cost tracking records something plausible."""
    return max(1, len(text) // CHARS_PER_TOKEN)
