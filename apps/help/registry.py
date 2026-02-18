from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.help.base import BaseHelpAgent

logger = logging.getLogger("ocs.help")

AGENT_REGISTRY: dict[str, type[BaseHelpAgent]] = {}


def register_agent(cls):
    if cls.name in AGENT_REGISTRY:
        logger.warning("Overwriting agent '%s' (was %s, now %s).", cls.name, AGENT_REGISTRY[cls.name], cls)
    AGENT_REGISTRY[cls.name] = cls
    return cls
