"""Abuse rules run on each human message. A rule returns its name when it fires, otherwise None."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.channels.datamodels import Attachment
    from apps.experiments.models import Participant

type Rule = Callable[[str, Sequence[Attachment], Participant | None], str | None]

MARKUP_PATTERN = re.compile(
    r"<script|<iframe|\bon\w+\s*=|javascript:|data:text/html|<svg|<img\b[^>]*\bsrc\s*=",
    re.IGNORECASE,
)


def markup(text: str, attachments: Sequence[Attachment], participant: Participant | None) -> str | None:
    """Fires on HTML or script markup that could run in a browser."""
    return "markup" if MARKUP_PATTERN.search(text) else None


RULES: tuple[Rule, ...] = (markup,)


def run_rules(*, text: str, attachments: Sequence[Attachment], participant: Participant | None) -> list[str]:
    """Names of the rules that fire for the message, in rule order."""
    return [name for rule in RULES if (name := rule(text, attachments, participant))]
