"""
Static flag definitions for Open Chat Studio.

This module contains all feature flag definitions with their descriptions and documentation URLs.
Developers should add new flags here when introducing new features.
"""

from dataclasses import dataclass
from enum import Enum


@dataclass
class FlagInfo:
    """Information about a feature flag."""

    name: str
    description: str
    docs_url: str = ""


class Flags(Enum):
    """All feature flags with their metadata."""

    # Core Features
    PIPELINES = FlagInfo("flag_pipelines", "Enables visual pipeline builder for complex chat workflows", "")

    PIPELINES_V2 = FlagInfo("flag_pipelines-v2", "Second version of pipeline functionality with enhanced features", "")

    CHATBOTS = FlagInfo("flag_chatbots", "Enables simplified chatbot creation and management interface", "")

    TEAM_DASHBOARD = FlagInfo("flag_team_dashboard", "Enables new team dashboard with analytics and overview", "")

    # AI & Voice Features
    OPEN_AI_VOICE_ENGINE = FlagInfo(
        "flag_open_ai_voice_engine", "Enables OpenAI voice synthesis for audio responses", ""
    )

    CHAT_STREAMING = FlagInfo("flag_chat_streaming", "Enables real-time streaming of chat responses", "")

    ASSISTANTS = FlagInfo("flag_assistants", "Enables OpenAI Assistants integration", "")

    # Document & Analysis Features
    DOCUMENT_MANAGEMENT = FlagInfo(
        "flag_document_management", "Enables advanced document upload and management features", ""
    )

    EXPERIMENT_RAG = FlagInfo("flag_experiment_rag", "Enables Retrieval-Augmented Generation for experiments", "")

    SESSION_ANALYSIS = FlagInfo("flag_session-analysis", "Enables detailed session analysis and reporting", "")

    EVALUATIONS = FlagInfo("flag_evaluations", "Enables experiment evaluation and testing framework", "")

    # System Features
    EVENTS = FlagInfo("flag_events", "Enables event-driven triggers and scheduled messages", "")

    EXPERIMENT_VERSIONS = FlagInfo(
        "flag_experiment_versions", "Enables versioning system for experiments and configurations", ""
    )

    SSO_LOGIN = FlagInfo("flag_sso_login", "Enables Single Sign-On authentication integration", "")

    # External Integrations
    COMMCARE_CONNECT = FlagInfo("flag_commcare_connect", "Enables integration with CommCare Connect platform", "")


def get_flag_info(flag_name: str) -> FlagInfo | None:
    """
    Get flag information by flag name.

    Args:
        flag_name: The name of the flag (e.g., "flag_chatbots")

    Returns:
        FlagInfo object if found, None otherwise
    """
    for flag in Flags:
        if flag.value.name == flag_name:
            return flag.value
    return None


def get_all_flag_info() -> dict[str, FlagInfo]:
    """
    Get all flag information as a dictionary.

    Returns:
        Dictionary mapping flag names to FlagInfo objects
    """
    return {flag.value.name: flag.value for flag in Flags}


def get_undefined_flags(existing_flag_names: list[str]) -> list[str]:
    """
    Get list of flag names that exist in database but not in Flags enum.

    Args:
        existing_flag_names: List of flag names from database

    Returns:
        List of flag names not defined in Flags enum
    """
    defined_flags = {flag.value.name for flag in Flags}
    return [name for name in existing_flag_names if name not in defined_flags]
