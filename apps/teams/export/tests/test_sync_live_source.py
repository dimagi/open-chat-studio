"""Syncing from a source that keeps receiving messages while the sync runs, as it does during the
cutover: parents created mid-run, rows the target creates or edits itself, and ``--follow``."""

import re
import signal
import threading
import uuid
from datetime import datetime

import pytest

from apps.api.export.views import CursorHelper
from apps.experiments.models import Experiment, Participant, ParticipantData
from apps.teams.export import seal as seal_mod
from apps.teams.export.importer import UnresolvedForeignKey
from apps.teams.export.tests.sync_fakes import PAST, FakeClient, make_manifest, select_chatbots, team_row
from apps.teams.export.translation import ALL_CHATBOTS_KEY, page_cursor
from apps.teams.management.commands import sync_team
from apps.teams.management.commands.sync_team import Command, run_sync
from apps.teams.models import Team
from apps.utils.factories.experiment import ExperimentFactory, ParticipantDataFactory, ParticipantFactory

pytestmark = pytest.mark.django_db


class _BusySourceClient(FakeClient):
    """Runs ``after_first_read[resource]`` once that resource has been read, to stand in for rows the
    source creates while the sync is running."""

    def __init__(self, manifest, rows_by_resource, after_first_read):
        super().__init__(manifest, rows_by_resource)
        self.after_first_read = dict(after_first_read)

    def iter_pages(self, resource, start_cursor=None, limit=100, selection=None):
        yield from super().iter_pages(resource, start_cursor, limit, selection)
        if hook := self.after_first_read.pop(resource, None):
            hook()


def _participant_entries(reread_participants=False):
    return [
        {
            "model": model,
            "resource": resource,
            "cursor": "updated_at_id",
            "secret": secret,
            "reread_under_selection": reread_participants and model == "experiments.participant",
        }
        for model, resource, secret in (
            ("experiments.participant", "participants", False),
            ("experiments.participantdata", "participant_data", True),
        )
    ]


def _participant_row(source_pk):
    return {
        "id": source_pk,
        "name": "",
        "identifier": f"user-{source_pk}",
        "public_id": str(uuid.uuid4()),
        "user": None,
        "platform": "web",
        "remote_id": "",
        "created_at": PAST,
        "updated_at": PAST,
    }


def _participant_data_row(source_pk, participant_pk, experiment_pk, public_key):
    return {
        "id": source_pk,
        "participant": participant_pk,
        "experiment": experiment_pk,
        "data": seal_mod.seal({"name": "late"}, public_key),
        "encryption_key": seal_mod.seal("", public_key),
        "system_metadata": {},
        "created_at": PAST,
        "updated_at": PAST,
    }


def _participant_scenario(public_key, make_store, tmp_path):
    """Participants and their data, with the chatbot they belong to already synced."""
    experiment = ExperimentFactory()
    store = make_store(tmp_path / "team.sqlite")
    store.record("experiments.experiment", 700, experiment.id)
    rows = {
        "teams": [team_row()],
        "participants": [_participant_row(1)],
        "participant_data": [_participant_data_row(10, 1, 700, public_key)],
    }
    return make_manifest(_participant_entries()), rows, store


def test_a_parent_created_after_its_resource_was_read_is_caught_up(make_store, tmp_path, keypair):
    """On a busy source, a participant and its data can both be created after participants were read.
    The data row then references a participant the sync hasn't seen, so participants are read again
    from their cursor before the data row is imported."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)

    def create_on_source():
        rows["participants"].append(_participant_row(2))
        rows["participant_data"].append(_participant_data_row(11, 2, 700, public_key))

    client = _BusySourceClient(manifest, rows, after_first_read={"participants": create_on_source})
    lines = []
    run_sync(client, store, private, write=lines.append, on_user_created=None)

    late = Participant.objects.get(pk=store.get_target("experiments.participant", 2))
    assert ParticipantData.objects.get(participant=late).data == {"name": "late"}
    assert client.iter_calls == [
        ("participants", None),
        ("participant_data", None),
        ("participants", page_cursor("updated_at_id", [_participant_row(1)])),
    ]
    assert lines == [
        "[1/2] participants: reading from the start",
        "[1/2] participants: page 1, 1 fetched, 1 synced (1 so far)",
        "[1/2] participants: synced 1 row",
        "[2/2] participant_data: reading from the start",
        "[2/2] participant_data: experiments.participantdata.participant -> experiments.participant:2 is not "
        "synced yet; re-reading [1/2] participants from its cursor",
        "[1/2] participants: caught up 1 row",
        "[2/2] participant_data: page 1, 2 fetched, 2 synced (2 so far)",
        "[2/2] participant_data: synced 2 rows",
    ]


def test_first_pass_reports_each_entry_and_page(make_store, tmp_path, keypair):
    """A large resource can take minutes to read, so progress is reported after every page, with the
    entry's position in the manifest."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    lines = []

    run_sync(FakeClient(manifest, rows), store, private, write=lines.append, on_user_created=None)

    assert lines == [
        "[1/2] participants: reading from the start",
        "[1/2] participants: page 1, 1 fetched, 1 synced (1 so far)",
        "[1/2] participants: synced 1 row",
        "[2/2] participant_data: reading from the start",
        "[2/2] participant_data: page 1, 1 fetched, 1 synced (1 so far)",
        "[2/2] participant_data: synced 1 row",
    ]


def test_a_parent_the_source_never_serves_still_raises_after_one_catch_up(make_store, tmp_path, keypair):
    """A parent that a catch-up doesn't bring in is outside the export, so the run stops instead of
    re-reading forever."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    rows["participant_data"].append(_participant_data_row(11, 99, 700, public_key))
    client = FakeClient(manifest, rows)

    with pytest.raises(UnresolvedForeignKey, match=re.escape("experiments.participant:99")):
        run_sync(client, store, private, on_user_created=None)

    assert [resource for resource, _cursor in client.iter_calls] == ["participants", "participant_data", "participants"]


def test_a_reference_to_a_later_resource_is_not_caught_up(make_store, tmp_path, keypair):
    """Only resources already read can be caught up; a reference to anything else is an ordering bug."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    manifest["entries"].reverse()
    client = FakeClient(manifest, rows)

    with pytest.raises(UnresolvedForeignKey, match=re.escape("experiments.participant:1")):
        run_sync(client, store, private, on_user_created=None)

    assert [resource for resource, _cursor in client.iter_calls] == ["participant_data"]


@pytest.mark.parametrize(
    ("target_has_data", "expected_data", "expected_adopted"),
    [
        pytest.param(
            False,
            {"name": "late"},
            {"experiments.participant": {1}},
            id="data-from-source",
        ),
        pytest.param(
            True,
            {"from": "target"},
            {"experiments.participant": {1}, "experiments.participantdata": {10}},
            id="data-kept-from-target",
        ),
    ],
)
def test_a_participant_the_target_created_during_cutover_is_adopted(
    make_store, tmp_path, keypair, target_has_data, expected_data, expected_adopted
):
    """Once a channel is switched, a user who messaged the source just before the switch can message
    the target before the sync has copied their participant. The source row is then mapped onto the
    target's participant instead of colliding with it on (team, platform, identifier)."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)

    # The first sync runs before the user has messaged the source.
    before_user_messaged = {**rows, "participants": [], "participant_data": []}
    run_sync(FakeClient(manifest, before_user_messaged), store, private, on_user_created=None)

    # After the switch, the user messages the target, which creates its own participant.
    team = Team.objects.get(slug="imported-team-z")
    on_target = ParticipantFactory(team=team, platform="web", identifier="user-1", name="Messaged the target")
    if target_has_data:
        experiment = Experiment.objects.get(pk=store.get_target("experiments.experiment", 700))
        ParticipantDataFactory(team=team, participant=on_target, experiment=experiment, data={"from": "target"})

    # The next sync reads the source's participant for the same user.
    importer = run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    assert store.get_target("experiments.participant", 1) == on_target.id
    assert list(Participant.objects.filter(team=team)) == [on_target]
    on_target.refresh_from_db()
    assert on_target.name == "Messaged the target"
    assert ParticipantData.objects.get(participant=on_target).data == expected_data
    assert importer.adopted_rows == expected_adopted


@pytest.fixture()
def keep_test_connection(monkeypatch):
    """A follow run recycles stale DB connections between passes, which would close the connection
    the test's transaction is open on."""
    monkeypatch.setattr(sync_team, "close_old_connections", lambda: None)


@pytest.mark.parametrize(
    ("edited_on_target", "expected_data", "expected_kept"),
    [
        pytest.param(False, {"name": "from source"}, {}, id="source-revision-applied"),
        pytest.param(True, {"name": "from target"}, {"experiments.participantdata": {10}}, id="target-edit-kept"),
    ],
)
def test_a_row_edited_on_the_target_is_not_overwritten_by_the_source(
    make_store, tmp_path, keypair, edited_on_target, expected_data, expected_kept
):
    """After a channel is switched, the source can still finish work it had queued for that
    conversation. Its later revision of a row must not overwrite what the target has since written."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    data = ParticipantData.objects.get(pk=store.get_target("experiments.participantdata", 10))
    if edited_on_target:
        data.data = {"name": "from target"}
        data.save()
    rows["participant_data"] = [
        {
            **_participant_data_row(10, 1, 700, public_key),
            "data": seal_mod.seal({"name": "from source"}, public_key),
            "updated_at": "2021-01-01T00:00:00+00:00",
        }
    ]

    importer = run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    data.refresh_from_db()
    assert data.data == expected_data
    assert importer.kept_target_edits == expected_kept


@pytest.mark.parametrize("adopted", [pytest.param(False, id="edited-on-target"), pytest.param(True, id="adopted")])
def test_a_kept_target_row_survives_later_source_revisions(make_store, tmp_path, keypair, adopted):
    """The source can revise a row more than once after the target took it over, each time later
    than the target's own write. None of those revisions overwrites the target's row."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    rows["participant_data"] = []
    team_and_no_participants = {**rows, "participants": []}
    run_sync(FakeClient(manifest, team_and_no_participants if adopted else rows), store, private, on_user_created=None)

    team = Team.objects.get(slug="imported-team-z")
    if adopted:
        on_target = ParticipantFactory(team=team, platform="web", identifier="user-1", name="from target")
    else:
        on_target = Participant.objects.get(pk=store.get_target("experiments.participant", 1))
        on_target.name = "from target"
        on_target.save()

    for revision in ["2099-01-01T00:00:00+00:00", "2099-01-02T00:00:00+00:00"]:
        rows["participants"] = [{**_participant_row(1), "name": f"source {revision}", "updated_at": revision}]
        run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    on_target.refresh_from_db()
    assert on_target.name == "from target"


@pytest.mark.parametrize(
    ("report_arg", "expected_line"),
    [
        pytest.param("adopted_rows", "experiments.participant: 2", id="adopted"),
        pytest.param("kept_target_edits", "experiments.participant: 2", id="kept-target-edits"),
    ],
)
def test_report_lists_rows_per_model(capsys, report_arg, expected_line):
    Command()._report(sync_complete=True, team_slug="acme", **{report_arg: {"experiments.participant": {1, 2}}})
    assert expected_line in capsys.readouterr().out


class _StopAfterWaits(threading.Event):
    """A stop event that is set by the ``waits``-th wait between passes, so a follow run ends without
    sleeping. ``between_passes`` runs on every wait, to stand in for writes on the source."""

    def __init__(self, waits, between_passes=lambda _number: None):
        super().__init__()
        self.waits = waits
        self.between_passes = between_passes
        self.intervals = []

    def wait(self, timeout=None):
        self.intervals.append(timeout)
        if len(self.intervals) >= self.waits:
            self.set()
        else:
            self.between_passes(len(self.intervals))
        return self.is_set()


class _CursorClient(FakeClient):
    """Serves the rows past the start cursor as one page, or an empty page when there are none, as the
    source does. ``limit`` is ignored."""

    def iter_pages(self, resource, start_cursor=None, limit=100, selection=None):
        rows = [row for page in super().iter_pages(resource, start_cursor, limit, selection) for row in page]
        yield [row for row in rows if _past_cursor(row, start_cursor)]


def _past_cursor(row, cursor):
    if cursor is None:
        return True
    if cursor.isdigit():
        return row["id"] > int(cursor)
    keyset = CursorHelper.decode(cursor)
    return (datetime.fromisoformat(row["updated_at"]), row["id"]) > (keyset["updated_at"], keyset["id"])


@pytest.mark.usefixtures("keep_test_connection")
def test_follow_keeps_syncing_until_stopped(make_store, tmp_path, keypair):
    """Rows the source creates between passes are synced by the next pass, which resumes from the
    cursors instead of reading everything again."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)

    def create_on_source(number):
        rows["participants"].append(_participant_row(1 + number))
        rows["participant_data"].append(_participant_data_row(10 + number, 1 + number, 700, public_key))

    stop = _StopAfterWaits(3, between_passes=create_on_source)
    client = _CursorClient(manifest, rows)
    lines = []
    run_sync(client, store, private, write=lines.append, on_user_created=None, follow=True, interval=5, stop=stop)

    assert stop.intervals == [5, 5, 5]
    assert ParticipantData.objects.filter(participant__identifier__in=["user-1", "user-2", "user-3"]).count() == 3
    assert [resource for resource, _cursor in client.iter_calls] == ["participants", "participant_data"] * 3
    first_pass_end = lines.index("[2/2] participant_data: synced 1 row") + 1
    later_pass = [
        "[1/2] participants: reading from the stored cursor",
        "[1/2] participants: page 1, 1 fetched, 1 synced (1 so far)",
        "[1/2] participants: synced 1 row",
        "[2/2] participant_data: reading from the stored cursor",
        "[2/2] participant_data: page 1, 1 fetched, 1 synced (1 so far)",
        "[2/2] participant_data: synced 1 row",
    ]
    assert lines[first_pass_end:] == [
        "pass 1 done: 2 rows synced, last new row 0:00:00 ago; next pass in 5s",
        "pass 2 started",
        *later_pass,
        "pass 2 done: 2 rows synced, last new row 0:00:00 ago; next pass in 5s",
        "pass 3 started",
        *later_pass,
        "pass 3 done: 2 rows synced, last new row 0:00:00 ago; next pass in 5s",
    ]


@pytest.mark.usefixtures("keep_test_connection")
def test_an_idle_follow_pass_reports_every_entry(make_store, tmp_path, keypair):
    """A pass that finds nothing still shows each entry it checked, as the first pass does."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    lines = []

    run_sync(
        _CursorClient(manifest, rows),
        store,
        private,
        write=lines.append,
        on_user_created=None,
        follow=True,
        stop=_StopAfterWaits(2),
    )

    first_pass_end = lines.index("[2/2] participant_data: synced 1 row") + 1
    assert lines[first_pass_end:] == [
        "pass 1 done: 2 rows synced, last new row 0:00:00 ago; next pass in 10s",
        "pass 2 started",
        "[1/2] participants: reading from the stored cursor",
        "[1/2] participants: page 1, 0 fetched, 0 synced (0 so far)",
        "[1/2] participants: synced 0 rows",
        "[2/2] participant_data: reading from the stored cursor",
        "[2/2] participant_data: page 1, 0 fetched, 0 synced (0 so far)",
        "[2/2] participant_data: synced 0 rows",
        "pass 2 done: 0 rows synced, last new row 0:00:00 ago; next pass in 10s",
    ]


@pytest.mark.usefixtures("keep_test_connection")
def test_follow_reads_flagged_resources_from_the_beginning_only_on_the_first_pass(make_store, tmp_path, keypair):
    """Re-reading every shared resource on every pass would rescan whole tables every few seconds."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    manifest["entries"] = _participant_entries(reread_participants=True)
    client = _CursorClient(manifest, select_chatbots(rows, "a"))

    run_sync(client, store, private, on_user_created=None, follow=True, stop=_StopAfterWaits(2))

    participant_reads = [cursor for resource, cursor in client.iter_calls if resource == "participants"]
    assert participant_reads == [None, page_cursor("updated_at_id", [_participant_row(1)])]


@pytest.mark.usefixtures("keep_test_connection")
def test_a_row_that_joins_the_selection_below_the_cursor_is_caught_up(make_store, tmp_path, keypair):
    """Under a selection, an existing participant joins the exported set when they start talking to a
    selected chatbot. Their row sits below the cursor, so after a catch-up from the cursor misses it,
    participants are read again from the beginning."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    manifest["entries"] = _participant_entries(reread_participants=True)
    rows["participants"].append(_participant_row(5))
    client = _CursorClient(manifest, select_chatbots(rows, "a"))

    def joins_the_selection(_number):
        rows["participants"].insert(1, _participant_row(3))
        rows["participant_data"].append(_participant_data_row(11, 3, 700, public_key))

    lines = []
    run_sync(
        client,
        store,
        private,
        write=lines.append,
        on_user_created=None,
        follow=True,
        stop=_StopAfterWaits(2, joins_the_selection),
    )

    assert ParticipantData.objects.filter(participant__identifier="user-3").exists()
    assert (
        "[2/2] participant_data: experiments.participantdata.participant -> experiments.participant:3 is not "
        "synced yet; re-reading [1/2] participants from the start"
    ) in lines
    cursor = page_cursor("updated_at_id", [_participant_row(5)])
    assert client.iter_calls[2:] == [
        ("participants", cursor),
        ("participant_data", page_cursor("updated_at_id", [_participant_data_row(10, 1, 700, public_key)])),
        ("participants", cursor),
        ("participants", None),
    ]


def test_a_stop_takes_effect_after_the_current_page(make_store, tmp_path, keypair):
    """The page being imported is finished and its cursor recorded, and nothing after it is read."""
    public_key, private = keypair
    manifest, rows, store = _participant_scenario(public_key, make_store, tmp_path)
    stop = threading.Event()

    class TwoPageClient(FakeClient):
        def iter_pages(self, resource, start_cursor=None, limit=100, selection=None):
            self.iter_calls.append((resource, start_cursor))
            stop.set()
            yield [_participant_row(1)]
            yield [_participant_row(2)]

    client = TwoPageClient(manifest, rows)
    lines = []
    run_sync(client, store, private, write=lines.append, on_user_created=None, follow=True, stop=stop)

    assert client.iter_calls == [("participants", None)]
    assert lines == [
        "[1/2] participants: reading from the start",
        "[1/2] participants: page 1, 1 fetched, 1 synced (1 so far)",
        "[1/2] participants: stopped after 1 row; the next run resumes from the stored cursor",
    ]
    assert store.has_target("experiments.participant", 1)
    assert not store.has_target("experiments.participant", 2)
    assert store.get_cursor(ALL_CHATBOTS_KEY, "experiments.participant") == page_cursor(
        "updated_at_id", [_participant_row(1)]
    )


@pytest.mark.parametrize(
    ("signum", "second_signal_handler"),
    [
        pytest.param(signal.SIGINT, signal.default_int_handler, id="ctrl-c"),
        pytest.param(signal.SIGTERM, signal.SIG_DFL, id="sigterm"),
    ],
)
def test_first_signal_requests_a_stop_and_a_second_one_aborts(signum, second_signal_handler):
    """The first Ctrl+C or SIGTERM lets the sync finish its page and print the report; the default
    handler is back for a second one, which ends the process at once."""
    previous = signal.getsignal(signum)
    stop = threading.Event()
    lines = []

    with Command()._stop_on_interrupt(stop, write=lines.append):
        signal.raise_signal(signum)
        assert stop.is_set()
        assert lines == ["Stopping after the current page. Press Ctrl+C again to abort immediately."]
        assert signal.getsignal(signum) is second_signal_handler

    assert signal.getsignal(signum) is previous
