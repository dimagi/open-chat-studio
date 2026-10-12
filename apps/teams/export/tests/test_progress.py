from datetime import datetime

from apps.teams.export.progress import timestamped


def test_timestamped_prefixes_each_line_with_the_time():
    lines = []
    write = timestamped(lines.append, now=lambda: datetime(2026, 1, 1, 9, 5, 7))
    write("[1/2] participants: reading from the start")
    assert lines == ["09:05:07 [1/2] participants: reading from the start"]
