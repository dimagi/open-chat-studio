"""The progress lines ``sync_team`` prints while it runs."""

from collections.abc import Callable
from datetime import datetime, timedelta

from django.core.management.color import Style

from apps.teams.export.importer import UnresolvedForeignKey


def timestamped(write: Callable[[str], None], now: Callable[[], datetime] = datetime.now) -> Callable[[str], None]:
    """Prefix each line with the time it was written, so the log shows when the sync was last active."""
    return lambda message: write(f"{now():%H:%M:%S} {message}")


def style_synced_line(message: str, count: int, style: Style | None) -> str:
    """Colour a progress line by how much it moved: green when rows were synced, muted (ANSI faint)
    when zero so the eye skips the no-op lines. Returns the message unchanged when no style is given
    or colour is disabled (--no-color / non-tty, where the style funcs are the identity), so
    redirected logs stay clean."""
    if style is None:
        return message
    if count > 0:
        return style.SUCCESS(message)
    if style.SUCCESS("") == "":  # colour disabled -- leave it plain rather than emit raw escapes
        return message
    return f"\x1b[2m{message}\x1b[0m"


def rows(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"


class SyncProgress:
    """Prints what the sync is doing, each line labelled with the entry's place in the manifest.

    Every pass reports every entry and page. A catch-up read reports only how many rows it brought in,
    so its pages don't interleave with the entry being read.
    """

    def __init__(self, write: Callable[[str], None], entries: list[dict], style: Style | None = None) -> None:
        self.write = write
        self.entries = entries
        self.style = style
        self.catch_ups_running = 0

    def entry_started(self, position: int, from_start: bool) -> None:
        self._line(position=position, message=f"reading from {'the start' if from_start else 'the stored cursor'}")

    def page_read(self, position: int, number: int, fetched: int, synced: int, total: int) -> None:
        if self.catch_ups_running:
            return
        self._line(position=position, message=f"page {number}, {fetched} fetched, {synced} synced ({total} so far)")

    def entry_finished(self, position: int, count: int, stopped: bool) -> None:
        if stopped:
            self._line(
                position=position,
                message=f"stopped after {rows(count)}; the next run resumes from the stored cursor",
            )
        else:
            message = f"{self._label(position)}: synced {rows(count)}"
            self.write(style_synced_line(message=message, count=count, style=self.style))

    def catching_up(self, position: int, error: UnresolvedForeignKey, parent_position: int, from_start: bool) -> None:
        self.catch_ups_running += 1
        start = "the start" if from_start else "its cursor"
        self._line(
            position=position,
            message=f"{error} is not synced yet; re-reading {self._label(parent_position)} from {start}",
        )

    def caught_up(self, parent_position: int, count: int) -> None:
        self.catch_ups_running -= 1
        self._line(position=parent_position, message=f"caught up {rows(count)}")

    def pass_started(self, number: int) -> None:
        self.write(f"pass {number} started")

    def pass_finished(self, number: int, count: int, quiet_for: timedelta, next_pass_in: int) -> None:
        message = (
            f"pass {number} done: {rows(count)} synced, last new row {quiet_for} ago; next pass in {next_pass_in}s"
        )
        self.write(style_synced_line(message=message, count=count, style=self.style))

    def _label(self, position: int) -> str:
        """The entry's place in the manifest and its resource, e.g. ``[36/50] chat_messages``."""
        return f"[{position + 1}/{len(self.entries)}] {self.entries[position]['resource']}"

    def _line(self, position: int, message: str) -> None:
        self.write(f"{self._label(position)}: {message}")
