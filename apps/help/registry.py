from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.help.base import BaseHelpAgent

AGENT_REGISTRY: dict[str, type[BaseHelpAgent]] = {}


def register_agent(cls):
    AGENT_REGISTRY[cls.name] = cls
    return cls
