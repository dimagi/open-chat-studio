import json


def text_message(session_id: str):
    return json.dumps({"message": "Hi there", "session_id": session_id})
