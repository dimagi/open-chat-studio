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
        "MessageType": "text",
    }


def _image_message(message: dict):
    message["MessageType"] = "image"
    message["MediaContentType0"] = "image/png"
    message["MediaUrl0"] = "http://example.com/media"
    return message


def _audio_message(message: dict):
    message["MessageType"] = "audio"
    message["MediaContentType0"] = "audio/ogg"
    message["MediaUrl0"] = "http://example.com/media"
    return message


def _document_message(message: dict, mime_type: str = "application/pdf"):
    message["MessageType"] = "document"
    message["MediaContentType0"] = mime_type
    message["MediaUrl0"] = "http://example.com/media"
    return message


class Whatsapp:
    to = "whatsapp:+14155238886"
    from_ = "whatsapp:+27456897512"
    bsuid = "US.13491208655302741918"

    @staticmethod
    def text_message():
        # Post-rollout (June 2026+) Twilio webhooks include the BSUID in ExternalUserId
        # (prefixed with the channel, like From/To) alongside the phone in From.
        message = _text_message(to=Whatsapp.to, from_=Whatsapp.from_)
        message["ExternalUserId"] = f"whatsapp:{Whatsapp.bsuid}"
        return message

    @staticmethod
    def image_message():
        return _image_message(Whatsapp.text_message())

    @staticmethod
    def audio_message():
        return _audio_message(Whatsapp.text_message())

    @staticmethod
    def document_message(mime_type: str = "application/pdf"):
        return _document_message(Whatsapp.text_message(), mime_type=mime_type)


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
