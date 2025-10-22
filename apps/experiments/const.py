from django.db import models
from django.utils.translation import gettext_lazy


class ParticipantAccessLevel(models.TextChoices):
    """Defines how participant access is controlled for an experiment/chatbot"""
    OPEN = "open", gettext_lazy("Open Access (Public)")
    ALLOW_LIST = "allow_list", gettext_lazy("Allow List")
    DENY_LIST = "deny_list", gettext_lazy("Deny List")


DEFAULT_CONSENT_TEXT = """
Welcome to this chatbot built on Open Chat Studio! 

The Chatbot is provided "as-is" and "as available." Open Chat Studio makes no warranties,
express or implied, regarding the Chatbot's accuracy, completeness, or availability.

You use the chatbot at your own risk. Open Chat Studio shall not be liable for any harm
or damages that may result from your use of the chatbot.

You understand and agree that any reliance on the Chatbot's responses is solely at your own
discretion and risk.

By selecting “I Agree” below, you indicate that: 

* You have read and understood the above information.
* You voluntarily agree to try out this chatbot.
* You are 18 years or older.
"""
