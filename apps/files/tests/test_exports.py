import csv
import io
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from apps.files.exports import csv_to_tempfile, report_progress, save_data_export
from apps.files.models import FilePurpose
from apps.utils.factories.team import TeamFactory


def test_csv_to_tempfile_returns_readable_csv_at_start():
    with csv_to_tempfile(lambda writer: writer.writerows([["a", "b"], ["1", "é"]])) as tmp:
        content = tmp.read().decode("utf-8")

    assert list(csv.reader(io.StringIO(content))) == [["a", "b"], ["1", "é"]]


@pytest.mark.django_db()
def test_save_data_export_stores_expiring_export():
    team = TeamFactory.create()
    before = timezone.now()

    file = save_data_export(
        team=team, name="out.csv", file=io.BytesIO(b"a,b\n"), content_type="text/csv", expires_in=timedelta(days=7)
    )

    assert file.team == team
    assert file.purpose == FilePurpose.DATA_EXPORT
    assert file.content_type == "text/csv"
    assert file.read_bytes() == b"a,b\n"
    assert file.expiry_date is not None
    assert before + timedelta(days=7) <= file.expiry_date <= timezone.now() + timedelta(days=7)


def test_report_progress_yields_every_item_but_reports_in_steps():
    """Reporting once per item would be one backend write per item on a large export."""
    items = [{"#": index} for index in range(250)]
    recorder = Mock()

    yielded = list(report_progress(items=items, total=len(items), recorder=recorder, noun="messages"))

    assert yielded == items
    assert recorder.set_progress.call_args.args == (250, 250)
    assert recorder.set_progress.call_args.kwargs == {"description": "Processed 250 of 250 messages"}
    assert recorder.set_progress.call_count <= 101
