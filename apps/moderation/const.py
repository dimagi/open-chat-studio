MODERATION_WAIT_TIMEOUT = 10
"""Seconds `ModerationResultStage` waits for the moderation task after the bot has answered."""

STRIKE_LIMIT = 3
"""Strikes at which a participant is blocked automatically, when the team has auto-block on."""

STRIKE_CATEGORIES = frozenset(
    {
        "sexual/minors",
        "illicit",
        "illicit/violent",
        "harassment/threatening",
        "hate/threatening",
    }
)
"""Provider categories that make a message `flagged`. Other flagged categories are recorded only."""

FLAGGED_MESSAGE = "Your message was flagged by our content moderation, so it will not be answered."
"""Reply to a flagged message when a reply in the conversation's language cannot be generated."""
