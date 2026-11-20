import html
import re
from unittest.mock import patch

from apps.ocs_notifications.formatting import message_html


def rendered_message(create_notification_path: str, build) -> str:
    """The text a reader sees for the message ``build`` passes to the ``create_notification`` patched at that path."""
    with patch(create_notification_path) as mock_create_notification:
        build()

    rendered = message_html(mock_create_notification.call_args.kwargs["message"])
    return html.unescape(re.sub(r"<[^>]+>", "", rendered))
