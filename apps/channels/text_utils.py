import re

import emoji

# The regex from https://stackoverflow.com/a/6041965 is used, but tweaked to remove capturing groups
URL_REGEX = r"(?:http|ftp|https):\/\/(?:[\w_-]+(?:(?:\.[\w_-]+)+))(?:[\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])"

# Matches [^2]: [citation_text](https://example.com)
MARKDOWN_REF_PATTERN = r"^\[(?P<ref>.+?)\]:\s*\[(?P<citation_text>[^\]]+)\]\((?P<citation_url>.*)\)"


def strip_urls_and_emojis(text: str) -> tuple[str, list[str]]:
    """Strips any URLs in `text` and appends them to the end of the text. Emoji's are filtered out"""
    text = emoji.replace_emoji(text, replace="")

    url_pattern = re.compile(URL_REGEX)
    urls = list(dict.fromkeys(url_pattern.findall(text)))
    text = url_pattern.sub("", text)

    return text, urls
