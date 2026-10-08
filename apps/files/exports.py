"""Helpers for user-triggered exports that run as a Celery task and store their output as a File.

See docs/agents/data_exports.md for the full pattern.
"""

import csv
import io
import math
import tempfile
from collections.abc import Callable, Iterable, Iterator
from datetime import timedelta
from typing import IO

from celery_progress.backend import ProgressRecorder
from django.core.files import File as DjangoFile
from django.utils import timezone

from apps.files.models import File, FilePurpose
from apps.teams.models import Team

EXPORT_FAILED_MESSAGE = "The export could not be completed."

_SPOOL_MAX_BYTES = 10 * 1024 * 1024  # a spooled export stays in memory below this size


def csv_to_tempfile(write: Callable[["csv._writer"], None]) -> "tempfile.SpooledTemporaryFile[bytes]":
    """Run *write* against a CSV writer backed by a binary temp file, and return that file seeked to 0.

    Use as a context manager. The file is binary so it can go straight to `save_data_export`.
    """
    tmp = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="wb+")  # noqa: SIM115
    text_wrapper = io.TextIOWrapper(tmp, encoding="utf-8", newline="")
    write(csv.writer(text_wrapper))
    text_wrapper.flush()
    # Release the wrapper without closing tmp; the caller still has to read it.
    text_wrapper.detach()
    tmp.seek(0)
    return tmp


def save_data_export(*, team: Team, name: str, file: IO[bytes], content_type: str, expires_in: timedelta) -> File:
    """Store *file* as an expiring data export for *team*.

    Pass the open temp file itself: storage reads it in chunks, so memory stays flat.
    """
    return File.objects.create(
        team=team,
        name=name,
        file=DjangoFile(file, name=name),
        content_type=content_type,
        purpose=FilePurpose.DATA_EXPORT,
        expiry_date=timezone.now() + expires_in,
    )


def report_progress[T](items: Iterable[T], total: int, recorder: ProgressRecorder, noun: str = "rows") -> Iterator[T]:
    """Yield *items*, reporting progress to *recorder* in steps of roughly one percent.

    Reporting per item would be one backend write per item, which on a large export is more
    traffic than the export itself.
    """
    step = max(1, math.ceil(total / 100))
    for current, item in enumerate(items, start=1):
        if current % step == 0 or current == total:
            recorder.set_progress(current, total, description=f"Processed {current} of {total} {noun}")
        yield item
