import json


def text_message(message="Hi there", session_id=None):
    return json.dumps({"message": message, "session": session_id})
