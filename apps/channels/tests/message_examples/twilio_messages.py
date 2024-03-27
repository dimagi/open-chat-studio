import json

prefixes = {"whatsapp": "whatsapp", "facebook": "messenger"}


def text_message(platform: str = "whatsapp"):
    return json.dumps(
        {
            "SmsMessageSid": "DDDDDDDDDDDDDdd",
            "NumMedia": "0",
            "ProfileName": "Chris Smit",
            "SmsSid": "CCCCCCCCCCCCCCCCCCCCCCCCCC",
            "WaId": "27456897512",
            "SmsStatus": "received",
            "Body": "Dobroye utro",
            "To": f"{prefixes[platform]}:14155238886",
            "NumSegments": "1",
            "ReferralNumMedia": "0",
            "MessageSid": "BBBBBBBBBB",
            "AccountSid": "AAAAAAAAAAAAA",
            "From": f"{prefixes[platform]}:27456897512",
            "ApiVersion": "2010-04-01",
        }
    )


def image_message(platform: str = "whatsapp"):
    message = text_message(platform)
    message_dict = json.loads(message)
    message_dict["MediaContentType0"] = "image/png"
    message_dict["MediaUrl0"] = "http://example.com/media"
    return json.dumps(message_dict)


def audio_message(platform: str = "whatsapp"):
    message = text_message(platform)
    message_dict = json.loads(message)
    message_dict["MediaContentType0"] = "audio/ogg"
    message_dict["MediaUrl0"] = "http://example.com/media"
    return json.dumps(message_dict)
