def _text_message(to: str, from_: str):
    return {
        "SmsMessageSid": "DDDDDDDDDDDDDdd",
        "NumMedia": "0",
        "ProfileName": "Chris Smit",
        "SmsSid": "CCCCCCCCCCCCCCCCCCCCCCCCCC",
        "WaId": "27456897512",
        "SmsStatus": "received",
        "Body": "Dobroye utro",
        "To": to,
        "NumSegments": "1",
        "ReferralNumMedia": "0",
        "MessageSid": "BBBBBBBBBB",
        "AccountSid": "AAAAAAAAAAAAA",
        "From": from_,
        "ApiVersion": "2010-04-01",
    }


def _image_message(message: dict):
    message["MediaContentType0"] = "image/png"
    message["MediaUrl0"] = "http://example.com/media"
    return message


def _audio_message(message: dict):
    message["MediaContentType0"] = "audio/ogg"
    message["MediaUrl0"] = "http://example.com/media"
    return message


class Whatsapp:
    to = "whatsapp:+14155238886"
    from_ = "whatsapp:+27456897512"

    @staticmethod
    def text_message():
        return _text_message(to=Whatsapp.to, from_=Whatsapp.from_)

    @staticmethod
    def image_message():
        return _image_message(Whatsapp.text_message())

    @staticmethod
    def audio_message():
        return _audio_message(Whatsapp.text_message())


class Messenger:
    to = "messenger:14155238886"
    from_ = "messenger:27456897512"

    @staticmethod
    def text_message():
        return _text_message(to=Messenger.to, from_=Messenger.from_)

    @staticmethod
    def image_message():
        return _image_message(Messenger.text_message())

    @staticmethod
    def audio_message():
        return _audio_message(Messenger.text_message())
