"""
Static flag definitions for Open Chat Studio.

This module contains all feature flag definitions with their descriptions and documentation URLs.
Developers should add new flags here when introducing new features.
"""

from dataclasses import dataclass, field
from enum import Enum

from django.conf import settings


@dataclass
class FlagInfo:
    """Information about a feature flag."""

    slug: str
    description: str
    docs_slug: str = ""
    requires: list[str] = field(default_factory=list)
    """Other feature flags that should be enabled with this flag."""

    teams_can_manage: bool = False
    """Whether team admins can enable / disable this flag themselves"""

    removed: bool = False
    """Whether this flag has been removed and therefore is no longer active"""


class Flags(FlagInfo, Enum):
    """All feature flags with their metadata."""

    OPEN_AI_VOICE_ENGINE = ("flag_open_ai_voice_engine", "Enables OpenAI voice synthesis for audio responses")

    SESSION_ANALYSIS = ("flag_session-analysis", "Enables detailed session analysis and reporting")

    EVENTS = ("flag_events", "Enables event-driven triggers and scheduled messages", "events", [], True)

    SSO_LOGIN = ("flag_sso_login", "Enables Single Sign-On authentication integration", "")

    COMMCARE_CONNECT = ("flag_commcare_connect", "Enables integration with CommCare Connect platform", "")

    TRACING = ("flag_tracing", "Enables users to see OCS traces", "", [], True)

    EVALS = ("flag_evaluations", "Chatbot Evaluations (beta)", "evals", [], True)

    MCP = ("flag_mcp", "MCP tool support for chatbots (alpha)")

    NOTIFICATIONS = ("flag_notifications", "User notifications")

    CHAT_WIDGET = ("flag_chat_widget", "Use embedded chat widget instead of full-page chat UI (POC)")
    
    TESTING_CUSTOM_ACTIONS = (
        "flag_custom_actions_test_endpoints",
        "Testing endpoints for custom actions (internal use only)",
    )

    @property
    def docs_url(self):
        docs_link = settings.DOCUMENTATION_LINKS.get(self.docs_slug, None)
        if docs_link:
            return f"{settings.DOCUMENTATION_BASE_URL}{docs_link}"
        return None


def get_flag_info(flag_name: str) -> Flags | None:
    """
    Get flag information by flag name.

    Args:
        flag_name: The name of the flag (e.g., "flag_chatbots")

    Returns:
        FlagInfo object if found, None otherwise
    """
    for flag in Flags:
        if flag.slug == flag_name:
            return flag
    return None


def get_all_flag_info() -> dict[str, Flags]:
    """
    Get all flag information as a dictionary.

    Returns:
        Dictionary mapping flag names to FlagInfo objects
    """
    return {flag.slug: flag for flag in Flags}
