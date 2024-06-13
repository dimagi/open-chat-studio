def make_session_external_id(channel_id: str, thread_ts: str):
    return f"{channel_id}:{thread_ts}"


def parse_session_external_id(external_id: str) -> tuple[str, str]:
    channel_id, thread_ts = external_id.split(":")
    return channel_id, thread_ts
