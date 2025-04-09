# See https://www.twilio.com/docs/whatsapp/guidance-whatsapp-media-messages
TWILIO = [
    # Images
    "image/jpeg",
    "image/png",
    "image/webp",
    # Audio
    # "audio/ogg", Requires opus codec. Leave out for now
    "audio/amr",
    "audio/3gpp",
    "audio/aac",
    "audio/mpeg",
    # Documents
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    # Video
    # "video/mp4",  # MP4 (required H.264 video codec and AAC audio). Leave out for now
]
