def inbound_message():
    return {"message_text": "Hi", "patient_id": 6225, "user_id": 24}


def outbound_message():
    return {
        "patient_Id": 6225,
        "message_Body": "Hello, I am a chatbot, how can I help?",
        "user_Id": 24,
        "title": "Sample Title",
    }
