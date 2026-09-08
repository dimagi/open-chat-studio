"""Tests for the scrub_participant_data management command."""

import logging
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.annotations.models import UserComment
from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.models import EvaluationMessage
from apps.experiments.management.commands.scrub_participant_data import (
    CHUNK_SIZE,
    SURFACES,
    ScrubPlan,
    parse_filter_query_string,
    resolve_selection,
    scrub_json,
    scrub_text,
    value_is_scannable,
)
from apps.experiments.models import ExperimentSession, Participant, ParticipantData
from apps.pipelines.models import PipelineChatHistory, PipelineChatHistoryTypes, PipelineChatMessages
from apps.trace.models import Trace
from apps.utils.factories.evaluations import EvaluationResultFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantDataFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory
from apps.utils.factories.user import UserFactory
from apps.web.dynamic_filters.base import Operators

COMMAND = "apps.experiments.management.commands.scrub_participant_data"
SECRET = "John Smith"

# The fixture's keys sort to ["age", "name"], so the prompts run in this order: the selection,
# the replacement for "age", the replacement for "name", then the typed confirmation.
SCRUB_BOTH_KEYS = ["all", "", "[REDACTED]", "SCRUB"]


def _plan(originals: dict, replacements: dict) -> ScrubPlan:
    return ScrubPlan(replacements=replacements, originals=originals)


def _add_records(session, *, participant=None):
    """Put SECRET (and a short value) on every surface reachable from one session."""
    participant = participant or session.participant
    message = ChatMessage.objects.create(
        chat=session.chat,
        message_type=ChatMessageType.HUMAN,
        content=f"Hi, {SECRET} here",
        summary=f"{SECRET} checked in",
        translations={"af": f"Haai, {SECRET} hier"},
    )
    trace = TraceFactory.create(
        team=session.team,
        experiment=session.experiment,
        session=session,
        participant=participant,
        participant_data={"name": SECRET, "age": 7},
        participant_data_diff=[["change", "name", [SECRET, "J"]]],
        session_state={"last_caller": SECRET, "age": 7},
        trace_metadata={"note": SECRET},
        error=f"{SECRET} timed out",
    )
    eval_message = EvaluationMessage.objects.create(
        session=session,
        input={"content": f"{SECRET} speaking", "role": "human"},
        output={"content": f"Hello {SECRET}", "role": "ai"},
        context={"who": SECRET},
        history=[{"message_type": "human", "content": f"{SECRET} again", "summary": ""}],
        participant_data={"name": SECRET, "age": 7},
        session_state={"last_caller": SECRET},
        metadata={"note": f"{SECRET} was here"},
    )
    pipeline_message = PipelineChatMessages.objects.create(
        chat_history=PipelineChatHistory.objects.create(
            session=session, type=PipelineChatHistoryTypes.NAMED, name="node-1"
        ),
        node_id="node-1",
        human_message=f"Hi, {SECRET} here",
        ai_message=f"Hello {SECRET}",
        summary=f"{SECRET} checked in",
    )
    return {
        "message": message,
        "trace": trace,
        "eval_message": eval_message,
        "pipeline_message": pipeline_message,
    }


def _add_evaluation_result(eval_message, *, team, generated_session=None):
    """An evaluator result, holding the copy of the message that `as_result_dict` embeds."""
    return EvaluationResultFactory.create(
        team=team,
        message=eval_message,
        session=generated_session,
        evaluator__team=team,
        run__team=team,
        output={
            "message": eval_message.as_result_dict(),
            "generated_response": f"Hello {SECRET}",
            "result": {"note": f"{SECRET} sounded upset"},
        },
    )


def _generated_session(experiment, *, on_working_version=False, **kwargs):
    """The throwaway session bot generation runs an evaluation message through.

    Owned by the synthetic "evaluations" participant. An evaluation configured for a specific
    or published version attaches it to that version row; one configured for the latest
    working version attaches it to the working chatbot itself.
    """
    target = experiment
    if not on_working_version:
        target = ExperimentFactory.create(team=experiment.team, working_version=experiment, version_number=99)
    return ExperimentSessionFactory.create(
        experiment=target,
        team=experiment.team,
        platform=ChannelPlatform.EVALUATIONS,
        participant__identifier="evaluations",
        participant__platform=ChannelPlatform.EVALUATIONS,
        participant__team=experiment.team,
        **kwargs,
    )


@pytest.fixture()
def chatbot(db):
    """One chatbot, one participant, one session, SECRET on every surface."""
    session = ExperimentSessionFactory.create(state={"last_caller": SECRET, "age": 7})
    session.participant.name = SECRET
    session.participant.identifier = "user1@example.com"
    session.participant.save()
    ParticipantDataFactory.create(
        team=session.team,
        experiment=session.experiment,
        participant=session.participant,
        data={"name": SECRET, "age": 7},
    )
    records = _add_records(session)
    return {"session": session, "experiment": session.experiment, "participant": session.participant, **records}


def _scrub(experiment, monkeypatch, *, answers=None, filter_query="", **kwargs):
    """Run the command, answering its prompts from ``answers`` in order."""
    responses = iter(SCRUB_BOTH_KEYS if answers is None else answers)
    monkeypatch.setattr("builtins.input", lambda *_args: next(responses))
    call_command("scrub_participant_data", experiment.team.slug, str(experiment.id), filter=filter_query, **kwargs)


def _participant_data(chatbot):
    return ParticipantData.objects.get(participant=chatbot["participant"], experiment=chatbot["experiment"])


def _totals(printed: str) -> dict[str, int]:
    """Per-surface counts parsed from the printed totals block ("messages" also matches "evaluation_messages")."""
    parsed = (line.strip().partition(": ") for line in printed.splitlines())
    return {label: int(count) for label, _, count in parsed if label in SURFACES}


def _other_participant(experiment, *, data=None, name=None, identifier=None, state=None):
    """A second participant of ``experiment``, with their own data, name and session state."""
    session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team, state=state or {})
    if name is not None:
        session.participant.name = name
    if identifier is not None:
        session.participant.identifier = identifier
    if name is not None or identifier is not None:
        session.participant.save()
    if data is not None:
        ParticipantDataFactory.create(
            team=experiment.team, experiment=experiment, participant=session.participant, data=data
        )
    return session


class TestTextReplacement:
    """The value-based primitive: finding a participant's own values in free text."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("John", "X", id="whole-string"),
            pytest.param("Hi John!", "Hi X!", id="punctuation-is-a-boundary"),
            pytest.param("name:John", "name:X", id="colon-is-a-boundary"),
            pytest.param("John-Smith", "X-Smith", id="hyphen-is-a-boundary"),
            pytest.param("JOHN", "X", id="case-insensitive"),
            pytest.param("Johnson", "Johnson", id="suffix-is-not-a-match"),
            pytest.param("stjohn@mail.com", "stjohn@mail.com", id="prefix-is-not-a-match"),
            pytest.param("John_Smith", "John_Smith", id="underscore-is-a-word-char"),
            pytest.param("John and John", "X and X", id="every-occurrence"),
        ],
    )
    def test_word_boundaries_and_case(self, text, expected):
        assert scrub_text(text, _plan({"n": "John"}, {"n": "X"})) == expected

    @pytest.mark.parametrize(
        ("value", "scannable"),
        [
            pytest.param("Bob", False, id="three-chars-too-short"),
            pytest.param("Bobb", True, id="four-chars-is-enough"),
            pytest.param(" Bob ", False, id="padding-does-not-count-toward-length"),
            pytest.param(7, False, id="int-is-never-scanned"),
            pytest.param(None, False, id="none-is-never-scanned"),
            pytest.param("", False, id="empty-is-never-scanned"),
        ],
    )
    def test_only_distinctive_values_are_scanned(self, value, scannable):
        assert value_is_scannable(value) is scannable

    @pytest.mark.parametrize(
        "value",
        [pytest.param("Bob", id="too-short-to-hunt-for"), pytest.param(7, id="non-string")],
    )
    def test_nothing_scannable_leaves_text_alone(self, value):
        assert scrub_text("Bob and Bobby", _plan({"n": value}, {"n": "X"})) == "Bob and Bobby"

    def test_stored_value_is_stripped_before_matching(self):
        """Participant data holding " John " should still clear "John" from prose."""
        assert scrub_text("a John b", _plan({"n": " John "}, {"n": "X"})) == "a X b"

    def test_longest_value_wins_when_one_is_a_prefix_of_another(self):
        plan = _plan({"full": "John Smith", "first": "John"}, {"full": "FULL", "first": "FIRST"})
        assert scrub_text("John Smith here", plan) == "FULL here"
        assert scrub_text("John alone", plan) == "FIRST alone"

    def test_replacements_do_not_cascade(self):
        """One key's replacement must not be re-matched by another key's rule.

        Scrubbing "Johnny" to "Robert" while a second key's value *is* "Robert" must leave
        "Robert" standing, not carry it on into the second rule.
        """
        plan = _plan({"a": "Johnny", "b": "Robert"}, {"a": "Robert", "b": "ZZZZ"})
        assert scrub_text("Johnny called", plan) == "Robert called"
        assert scrub_text("Robert called", plan) == "ZZZZ called"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("A. Smith (Jr.)", id="dots-and-parens"),
            pytest.param("foo|bar", id="alternation"),
            pytest.param("a+b+c", id="quantifier"),
            pytest.param("[bracketed]", id="character-class"),
        ],
    )
    def test_regex_metacharacters_in_a_value_are_literal(self, value):
        assert scrub_text(f"call {value} now", _plan({"n": value}, {"n": "X"})) == "call X now"

    @pytest.mark.parametrize(
        "replacement",
        [
            pytest.param(r"\1", id="group-reference"),
            pytest.param(r"\g<0>", id="named-group-reference"),
            pytest.param("back\\slash", id="backslash"),
            pytest.param("$1", id="dollar-one"),
        ],
    )
    def test_replacement_string_is_inserted_literally(self, replacement):
        assert scrub_text("Hi John", _plan({"n": "John"}, {"n": replacement})) == f"Hi {replacement}"

    def test_empty_replacement_deletes_the_value(self):
        assert scrub_text("Hi John there", _plan({"n": "John"}, {"n": ""})) == "Hi  there"


class TestJsonReplacement:
    """The key-based primitive: clearing a selected key's value at any depth."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param({"name": "John"}, {"name": "X"}, id="top-level-key"),
            pytest.param({"a": {"name": "John"}}, {"a": {"name": "X"}}, id="nested-dict"),
            pytest.param({"a": [{"name": "John"}]}, {"a": [{"name": "X"}]}, id="dict-in-list-in-dict"),
            pytest.param([{"name": 7}], [{"name": "X"}], id="key-clears-non-string-value"),
            pytest.param({"other": "John"}, {"other": "X"}, id="unselected-key-still-text-scanned"),
            pytest.param({"John": "John"}, {"John": "X"}, id="dict-keys-are-never-rewritten"),
            pytest.param(
                {"a": 1, "b": 2.5, "c": True, "d": None},
                {"a": 1, "b": 2.5, "c": True, "d": None},
                id="non-string-leaves-preserved",
            ),
            pytest.param(
                [["change", "name", ["John", "J"]]],
                [["change", "name", ["X", "J"]]],
                id="top-level-list-dictdiffer-shape",
            ),
            pytest.param({}, {}, id="empty-dict"),
            pytest.param({"a": []}, {"a": []}, id="empty-list"),
        ],
    )
    def test_shapes(self, value, expected):
        assert scrub_json(value, _plan({"name": "John"}, {"name": "X"})) == expected

    def test_tuples_become_lists(self):
        """JSON has no tuple, so a tuple in a Python-side value round-trips as a list."""
        assert scrub_json({"a": ("John",)}, _plan({"name": "John"}, {"name": "X"})) == {"a": ["X"]}

    def test_a_selected_key_with_no_original_still_clears_its_value(self):
        """A key nobody holds is still cleared wherever it appears in stored JSON."""
        assert scrub_json({"absent": "anything"}, _plan({}, {"absent": "X"})) == {"absent": "X"}


@pytest.mark.django_db()
class TestSurfaceCoverage:
    """Every field one run rewrites, and the rows it reaches."""

    def test_scrubs_every_surface(self, chatbot, monkeypatch):
        """One changed row on every surface the command reports, so a new surface has to be covered here."""
        chat = chatbot["session"].chat
        chat.name = f"{SECRET} - web"
        chat.save()
        generated = _generated_session(chatbot["experiment"], state={"last_caller": SECRET, "age": 7})
        result = _add_evaluation_result(
            chatbot["eval_message"], team=chatbot["experiment"].team, generated_session=generated
        )
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        assert Chat.objects.get(id=chat.id).name == "[REDACTED] - web"

        pipeline_message = PipelineChatMessages.objects.get(id=chatbot["pipeline_message"].id)
        assert pipeline_message.human_message == "Hi, [REDACTED] here"
        assert pipeline_message.ai_message == "Hello [REDACTED]"
        assert pipeline_message.summary == "[REDACTED] checked in"

        result.refresh_from_db()
        assert result.output["message"]["input"]["content"] == "[REDACTED] speaking"
        assert result.output["message"]["participant_data"] == {"name": "[REDACTED]", "age": ""}
        assert result.output["generated_response"] == "Hello [REDACTED]"
        assert result.output["result"] == {"note": "[REDACTED] sounded upset"}

        assert ExperimentSession.objects.get(id=generated.id).state == {"last_caller": "[REDACTED]", "age": ""}

        message = ChatMessage.objects.get(id=chatbot["message"].id)
        assert message.content == "Hi, [REDACTED] here"
        assert message.summary == "[REDACTED] checked in"
        assert message.translations == {"af": "Haai, [REDACTED] hier"}

        session = ExperimentSession.objects.get(id=chatbot["session"].id)
        assert session.state == {"last_caller": "[REDACTED]", "age": ""}

        trace = Trace.objects.get(id=chatbot["trace"].id)
        assert trace.participant_data == {"name": "[REDACTED]", "age": ""}
        assert trace.participant_data_diff == [["change", "name", ["[REDACTED]", "J"]]]
        assert trace.session_state == {"last_caller": "[REDACTED]", "age": ""}
        assert trace.trace_metadata == {"note": "[REDACTED]"}
        assert trace.error == "[REDACTED] timed out"

        eval_message = EvaluationMessage.objects.get(id=chatbot["eval_message"].id)
        assert eval_message.input == {"content": "[REDACTED] speaking", "role": "human"}
        assert eval_message.output == {"content": "Hello [REDACTED]", "role": "ai"}
        assert eval_message.context == {"who": "[REDACTED]"}
        assert eval_message.history == [{"message_type": "human", "content": "[REDACTED] again", "summary": ""}]
        assert eval_message.participant_data == {"name": "[REDACTED]", "age": ""}
        assert eval_message.session_state == {"last_caller": "[REDACTED]"}
        assert eval_message.metadata == {"note": "[REDACTED] was here"}

        assert Participant.objects.get(id=chatbot["participant"].id).name == "[REDACTED]"
        assert _participant_data(chatbot).data == {"name": "[REDACTED]", "age": ""}

        printed = output.getvalue()
        assert _totals(printed) == dict.fromkeys(SURFACES, 1)

    def test_a_participant_name_holding_another_keys_value_is_text_scanned(self, chatbot, monkeypatch):
        """The name is replaced wholesale when "name" is selected, and scanned otherwise."""
        data = _participant_data(chatbot)
        data.data = {"name": SECRET, "age": 7, "nickname": "Zedediah"}
        data.save()
        chatbot["participant"].name = "Zedediah rocks"
        chatbot["participant"].save()

        # keys sort to ["age", "name", "nickname"]; select nickname only
        _scrub(chatbot["experiment"], monkeypatch, answers=["3", "NICK", "SCRUB"])

        assert Participant.objects.get(id=chatbot["participant"].id).name == "NICK rocks"

    def test_an_empty_participant_name_is_left_alone(self, chatbot, monkeypatch):
        chatbot["participant"].name = ""
        chatbot["participant"].save()
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        assert Participant.objects.get(id=chatbot["participant"].id).name == ""
        assert "participant_names: 0" in output.getvalue()

    @pytest.mark.parametrize(
        ("on_older_version", "archived"),
        [
            pytest.param(False, False, id="working-version"),
            pytest.param(True, False, id="older-version"),
            pytest.param(True, True, id="archived-older-version"),
        ],
    )
    def test_a_trace_whose_session_was_deleted_is_still_scrubbed(
        self, chatbot, monkeypatch, on_older_version, archived
    ):
        """Trace.session is SET_NULL, so an orphaned trace still holds a copy.

        The orphan clause searches the whole version family, archived versions included: the
        default related manager drops those, and their traces hold the same data.
        """
        experiment = chatbot["experiment"]
        if on_older_version:
            experiment = ExperimentFactory.create(
                team=experiment.team, working_version=experiment, version_number=1, is_archived=archived
            )
        orphan = TraceFactory.create(
            team=chatbot["experiment"].team,
            experiment=experiment,
            session=None,
            participant=chatbot["participant"],
            participant_data={"name": SECRET},
            session_state={"last_caller": SECRET},
        )

        _scrub(chatbot["experiment"], monkeypatch)

        orphan.refresh_from_db()
        assert orphan.participant_data == {"name": "[REDACTED]"}
        assert orphan.session_state == {"last_caller": "[REDACTED]"}

    def test_an_evaluation_message_with_no_session_is_out_of_reach(self, chatbot, monkeypatch):
        """A documented limitation: CSV-imported messages have no session to reach them by."""
        orphan = EvaluationMessage.objects.create(session=None, input={"content": f"{SECRET} speaking"})

        _scrub(chatbot["experiment"], monkeypatch)

        orphan.refresh_from_db()
        assert orphan.input == {"content": f"{SECRET} speaking"}

    def test_writes_every_row_past_the_batch_boundary(self, chatbot, monkeypatch):
        """The write batches flush at CHUNK_SIZE, so the boundary is a real code path."""
        ChatMessage.objects.bulk_create(
            [
                ChatMessage(chat=chatbot["session"].chat, message_type=ChatMessageType.HUMAN, content=f"{SECRET} #{n}")
                for n in range(CHUNK_SIZE + 1)
            ]
        )

        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        assert not ChatMessage.objects.filter(content__icontains=SECRET).exists()
        # The fixture's own message brings the total to one past two full batches.
        assert _totals(output.getvalue())["messages"] == CHUNK_SIZE + 2

    @pytest.mark.parametrize("on_working_version", [False, True], ids=["on-a-version", "on-the-working-chatbot"])
    def test_scrubs_the_messages_copied_into_a_generation_session(self, chatbot, monkeypatch, on_working_version):
        """Bot generation replays the participant's history into the throwaway session's chat."""
        generated = _generated_session(chatbot["experiment"], on_working_version=on_working_version, state={})
        copied = ChatMessage.objects.create(
            chat=generated.chat, message_type=ChatMessageType.HUMAN, content=f"{SECRET} again"
        )
        _add_evaluation_result(chatbot["eval_message"], team=chatbot["experiment"].team, generated_session=generated)

        _scrub(chatbot["experiment"], monkeypatch)

        assert ChatMessage.objects.get(id=copied.id).content == "[REDACTED] again"

    def test_scrubs_the_chat_name_built_from_the_participant_identifier(self, chatbot, monkeypatch):
        """`Chat.name` is "<identifier> - <channel>", so it holds a copy of the identifier."""
        data = _participant_data(chatbot)
        data.data = {"email": "user1@example.com"}
        data.save()
        chat = chatbot["session"].chat
        chat.name = "user1@example.com - web"
        chat.save()

        _scrub(chatbot["experiment"], monkeypatch, answers=["1", "[GONE]", "SCRUB"])

        chat.refresh_from_db()
        assert chat.name == "[GONE] - web"


@pytest.mark.django_db()
class TestScope:
    """Which participants the filter selects, and which of their sessions get scrubbed."""

    def test_filter_selects_participants_not_sessions(self, chatbot, monkeypatch):
        """Matching one of a participant's sessions scrubs all of them.

        The values belong to the participant, not the session, so a narrow filter must not
        leave copies behind in their other conversations with this chatbot.
        """
        others = [
            ExperimentSessionFactory.create(
                experiment=chatbot["experiment"],
                team=chatbot["experiment"].team,
                participant=chatbot["participant"],
                state={"last_caller": SECRET},
            )
            for _ in range(2)
        ]
        records = [_add_records(session, participant=chatbot["participant"]) for session in others]

        _scrub(
            chatbot["experiment"],
            monkeypatch,
            filter_query=f"f_session_id={chatbot['session'].external_id}&op_session_id={Operators.EQUALS}",
        )

        for session, record in zip(others, records, strict=True):
            assert ExperimentSession.objects.get(id=session.id).state == {"last_caller": "[REDACTED]"}
            assert SECRET not in ChatMessage.objects.get(id=record["message"].id).content

    def test_unmatched_participants_are_untouched(self, chatbot, monkeypatch):
        other = _other_participant(chatbot["experiment"], identifier="user2@example.com", name=SECRET)
        other_records = _add_records(other)

        _scrub(
            chatbot["experiment"],
            monkeypatch,
            filter_query=f"f_participant=user1&op_participant={Operators.CONTAINS}",
        )

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == "Hi, [REDACTED] here"
        assert ChatMessage.objects.get(id=other_records["message"].id).content == f"Hi, {SECRET} here"
        assert Participant.objects.get(id=other.participant.id).name == SECRET

    def test_empty_filter_covers_every_participant(self, chatbot, monkeypatch):
        other = _other_participant(
            chatbot["experiment"], data={"name": SECRET, "age": 7}, state={"last_caller": SECRET}
        )
        other_records = _add_records(other)

        _scrub(chatbot["experiment"], monkeypatch)

        assert SECRET not in ChatMessage.objects.get(id=other_records["message"].id).content
        assert ExperimentSession.objects.get(id=other.id).state == {"last_caller": "[REDACTED]"}

    def test_the_same_participants_other_chatbots_are_untouched(self, chatbot, monkeypatch):
        """Participant data is per (participant, experiment), so a second chatbot keeps its copy."""
        other_session = ExperimentSessionFactory.create(
            team=chatbot["experiment"].team, participant=chatbot["participant"], state={"last_caller": SECRET}
        )
        other_data = ParticipantDataFactory.create(
            team=chatbot["experiment"].team,
            experiment=other_session.experiment,
            participant=chatbot["participant"],
            data={"name": SECRET, "age": 7},
        )
        other_records = _add_records(other_session, participant=chatbot["participant"])

        _scrub(chatbot["experiment"], monkeypatch)

        assert ParticipantData.objects.get(id=other_data.id).data == {"name": SECRET, "age": 7}
        assert ExperimentSession.objects.get(id=other_session.id).state == {"last_caller": SECRET}
        assert ChatMessage.objects.get(id=other_records["message"].id).content == f"Hi, {SECRET} here"

    def test_another_teams_records_are_untouched(self, chatbot, monkeypatch):
        team = TeamFactory.create()
        foreign = ExperimentSessionFactory.create(team=team, experiment__team=team, state={"last_caller": SECRET})
        ParticipantDataFactory.create(
            team=team, experiment=foreign.experiment, participant=foreign.participant, data={"name": SECRET}
        )
        foreign_records = _add_records(foreign)

        _scrub(chatbot["experiment"], monkeypatch)

        assert ExperimentSession.objects.get(id=foreign.id).state == {"last_caller": SECRET}
        assert ChatMessage.objects.get(id=foreign_records["message"].id).content == f"Hi, {SECRET} here"

    def test_a_version_id_resolves_to_the_working_chatbot(self, chatbot, monkeypatch):
        """Sessions attach to the working version, so a version id must scrub the family's data."""
        version = ExperimentFactory.create(
            team=chatbot["experiment"].team, working_version=chatbot["experiment"], version_number=1
        )

        _scrub(version, monkeypatch)

        assert _participant_data(chatbot).data == {"name": "[REDACTED]", "age": ""}
        assert ChatMessage.objects.get(id=chatbot["message"].id).content == "Hi, [REDACTED] here"

    def test_each_participant_is_scanned_for_only_their_own_values(self, chatbot, monkeypatch):
        """The plan is rebuilt per participant, so A's value is never hunted in B's records.

        Both participants' messages name both people; only the owner's own value may go.
        """
        other = _other_participant(chatbot["experiment"], data={"name": "Mary Jones"}, identifier="user2@example.com")
        shared_text = f"{SECRET} met Mary Jones"
        mine = ChatMessage.objects.create(
            chat=chatbot["session"].chat, message_type=ChatMessageType.HUMAN, content=shared_text
        )
        theirs = ChatMessage.objects.create(chat=other.chat, message_type=ChatMessageType.HUMAN, content=shared_text)

        _scrub(chatbot["experiment"], monkeypatch)

        assert ChatMessage.objects.get(id=mine.id).content == "[REDACTED] met Mary Jones"
        assert ChatMessage.objects.get(id=theirs.id).content == f"{SECRET} met [REDACTED]"

    def test_a_participant_without_data_is_not_text_scanned(self, chatbot, monkeypatch):
        """The values to search for come from each participant's own data.

        A participant holding no participant data has nothing to scan for, so free text naming
        someone else is left alone. Only the selected keys are cleared from their JSON.
        """
        other = _other_participant(chatbot["experiment"], state={"last_caller": SECRET, "age": 7})
        other_records = _add_records(other)

        original_name = other.participant.name

        _scrub(chatbot["experiment"], monkeypatch)

        assert ChatMessage.objects.get(id=other_records["message"].id).content == f"Hi, {SECRET} here"
        assert ExperimentSession.objects.get(id=other.id).state == {"last_caller": SECRET, "age": ""}
        # The "name" replacement is the global selection; this participant holds no name of
        # their own, and Participant.name is team-scoped, so it must not be rewritten.
        assert Participant.objects.get(id=other.participant.id).name == original_name

    def test_a_name_is_only_replaced_for_participants_who_hold_one(self, chatbot, monkeypatch):
        """Selecting "name" must not blank the name of a participant whose data lacks it."""
        other = _other_participant(chatbot["experiment"], data={"age": 41}, name="Someone Else")

        _scrub(chatbot["experiment"], monkeypatch)

        assert Participant.objects.get(id=chatbot["participant"].id).name == "[REDACTED]"
        assert Participant.objects.get(id=other.participant.id).name == "Someone Else"

    def test_another_participants_evaluator_result_is_untouched(self, chatbot, monkeypatch):
        """An evaluator result is reached through its message, so it follows that participant."""
        other = _other_participant(chatbot["experiment"], identifier="user2@example.com", data={"name": SECRET})
        other_records = _add_records(other)
        team = chatbot["experiment"].team
        mine = _add_evaluation_result(chatbot["eval_message"], team=team)
        theirs = _add_evaluation_result(other_records["eval_message"], team=team)

        _scrub(
            chatbot["experiment"],
            monkeypatch,
            filter_query=f"f_participant=user1&op_participant={Operators.CONTAINS}",
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.output["message"]["input"]["content"] == "[REDACTED] speaking"
        assert theirs.output["message"]["input"]["content"] == f"{SECRET} speaking"

    def test_another_participants_generation_session_is_untouched(self, chatbot, monkeypatch):
        """Generation sessions all belong to one synthetic participant, so only the link scopes them."""
        other = _other_participant(chatbot["experiment"], identifier="user2@example.com", data={"name": SECRET})
        other_records = _add_records(other)
        theirs = _generated_session(chatbot["experiment"], state={"last_caller": SECRET})
        _add_evaluation_result(other_records["eval_message"], team=chatbot["experiment"].team, generated_session=theirs)

        _scrub(
            chatbot["experiment"],
            monkeypatch,
            filter_query=f"f_participant=user1&op_participant={Operators.CONTAINS}",
        )

        assert ExperimentSession.objects.get(id=theirs.id).state == {"last_caller": SECRET}

    def test_the_evaluations_participants_own_sessions_are_never_matched(self, chatbot, monkeypatch):
        """A generation session on the working chatbot is visible to the filter.

        The synthetic participant owns every generation session of every evaluation run, and
        those hold copies of whatever the dataset contained, from any chatbot in the team. An
        empty filter must not select it; its sessions are reached only through the evaluator
        result that links each one to the participant it copied.
        """
        theirs = _generated_session(
            chatbot["experiment"], on_working_version=True, state={"name": "Someone Else", "last_caller": SECRET}
        )
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        assert ExperimentSession.objects.get(id=theirs.id).state == {"name": "Someone Else", "last_caller": SECRET}
        assert "Participants: 1" in output.getvalue()


@pytest.mark.django_db()
class TestScopeBoundary:
    """Records sitting next to the scrubbed ones, left alone by decision rather than omission."""

    def test_adjacent_surfaces_are_left_alone(self, chatbot, monkeypatch):
        """`ChatMessage.metadata` and `Chat.metadata` hold file references and routing state
        rather than participant prose, and a `UserComment` is a staff user's own words about
        the conversation, so scrubbing it would destroy someone else's writing.
        """
        message = chatbot["message"]
        message.metadata = {"note": SECRET}
        message.save()
        chat = chatbot["session"].chat
        chat.metadata = {"note": SECRET}
        chat.save()
        comment = UserComment.objects.create(
            content_object=chat, user=UserFactory.create(), comment=f"{SECRET} called in", team=chat.team
        )

        _scrub(chatbot["experiment"], monkeypatch)

        assert ChatMessage.objects.get(id=message.id).metadata == {"note": SECRET}
        assert Chat.objects.get(id=chat.id).metadata == {"note": SECRET}
        assert UserComment.objects.get(id=comment.id).comment == f"{SECRET} called in"


class TestKeySelectionPrompt:
    """Turning the typed answer into a list of keys."""

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            pytest.param("all", ["age", "name"], id="all"),
            pytest.param("ALL", ["age", "name"], id="all-uppercase"),
            pytest.param("1,2", ["age", "name"], id="both-numbers"),
            pytest.param(" 1 , 2 ", ["age", "name"], id="whitespace-tolerated"),
            pytest.param("2", ["name"], id="single"),
            pytest.param("2,1", ["name", "age"], id="order-follows-the-answer"),
            pytest.param("1,1", ["age"], id="duplicates-collapse"),
            pytest.param("1,,2", ["age", "name"], id="empty-token-ignored"),
        ],
    )
    def test_accepted_answers(self, answer, expected):
        assert resolve_selection(answer, ["age", "name"]) == expected

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace-only"),
            pytest.param(",", id="only-separators"),
        ],
    )
    def test_selecting_nothing_is_refused(self, answer):
        with pytest.raises(CommandError, match="No keys selected"):
            resolve_selection(answer, ["age", "name"])

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param("0", id="zero"),
            pytest.param("3", id="past-the-end"),
            pytest.param("-1", id="negative"),
            pytest.param("abc", id="not-a-number"),
            pytest.param("1,99", id="one-good-one-bad"),
            pytest.param("1.5", id="decimal"),
        ],
    )
    def test_answers_outside_the_list_are_refused(self, answer):
        with pytest.raises(CommandError, match="not one of the listed numbers"):
            resolve_selection(answer, ["age", "name"])


@pytest.mark.django_db()
class TestPromptFlow:
    """What the operator is shown, and what stops the run before it writes."""

    def test_offers_keys_with_counts_and_never_values(self, chatbot, monkeypatch):
        output = StringIO()
        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        printed = output.getvalue()
        assert "1. age: 1 participant(s) — 1 too short/non-text to scan for in prose" in printed
        assert "2. name: 1 participant(s)" in printed
        assert SECRET not in printed
        assert "also used as a structural key" not in printed

    def test_warns_when_a_selected_key_also_names_a_json_field(self, chatbot, monkeypatch):
        """Key-based replacement matches at any depth, so "content" also blanks message bodies."""
        data = _participant_data(chatbot)
        data.data = {"content": "a private note"}
        data.save()
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, answers=["1", "X", "SCRUB"], stdout=output)

        assert "also used as a structural key" in output.getvalue()
        assert EvaluationMessage.objects.get(id=chatbot["eval_message"].id).input["content"] == "X"

    def test_a_key_only_some_participants_can_be_scanned_for_says_so(self, chatbot, monkeypatch):
        """A mixed population must not be reported as if nothing gets text-scanned."""
        _other_participant(chatbot["experiment"], data={"name": "Bo"}, identifier="user2@example.com")
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        printed = output.getvalue()
        assert "name -> [REDACTED] — text-scanned for 1 of 2 participants, key-only for the rest" in printed
        assert "age -> (cleared) — cleared by key only, not text-scanned" in printed

    def test_the_plan_says_a_key_is_cleared_even_for_participants_who_never_held_it(self, chatbot, monkeypatch):
        """The key pass is global, so the participant counts are not the blast radius."""
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        assert "including for in-scope participants who never held it" in output.getvalue()

    def test_a_non_interactive_run_fails_cleanly(self, chatbot, monkeypatch):
        """A bare EOFError traceback would leave the operator guessing."""

        def no_answer(*_args):
            raise EOFError

        monkeypatch.setattr("builtins.input", no_answer)

        with pytest.raises(CommandError, match="answered interactively"):
            call_command("scrub_participant_data", chatbot["experiment"].team.slug, str(chatbot["experiment"].id))

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"

    def test_selecting_one_key_leaves_the_others_alone(self, chatbot, monkeypatch):
        _scrub(chatbot["experiment"], monkeypatch, answers=["1", "", "SCRUB"])

        assert _participant_data(chatbot).data == {"name": SECRET, "age": ""}
        assert Participant.objects.get(id=chatbot["participant"].id).name == SECRET

    def test_each_replacement_is_paired_with_the_key_it_was_asked_for(self, chatbot, monkeypatch):
        """The prompts follow the selection order, so a mis-pairing would swap the values."""
        _scrub(chatbot["experiment"], monkeypatch, answers=["2,1", "NAME", "AGE", "SCRUB"])

        assert _participant_data(chatbot).data == {"name": "NAME", "age": "AGE"}

    def test_a_bad_selection_writes_nothing(self, chatbot, monkeypatch):
        with pytest.raises(CommandError):
            _scrub(chatbot["experiment"], monkeypatch, answers=["99"])

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"

    @pytest.mark.parametrize(
        "confirmation",
        [
            pytest.param("nope", id="wrong-word"),
            pytest.param("", id="empty"),
            pytest.param("scrub", id="lowercase-is-not-enough"),
        ],
    )
    def test_a_failed_confirmation_writes_nothing(self, chatbot, monkeypatch, confirmation):
        with pytest.raises(CommandError, match="Aborted"):
            _scrub(chatbot["experiment"], monkeypatch, answers=["all", "", "[REDACTED]", confirmation])

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"
        assert _participant_data(chatbot).data == {"name": SECRET, "age": 7}

    def test_no_participant_data_stops_before_prompting(self, db, monkeypatch):
        session = ExperimentSessionFactory.create(state={"last_caller": SECRET})
        _add_records(session)
        output = StringIO()

        _scrub(session.experiment, monkeypatch, answers=[], stdout=output)

        assert "no participant data" in output.getvalue()
        assert ExperimentSession.objects.get(id=session.id).state == {"last_caller": SECRET}

    def test_a_filter_matching_nothing_stops_before_prompting(self, chatbot, monkeypatch):
        output = StringIO()

        _scrub(
            chatbot["experiment"],
            monkeypatch,
            answers=[],
            filter_query=f"f_participant=nobody-at-all&op_participant={Operators.CONTAINS}",
            stdout=output,
        )

        assert "matched no sessions" in output.getvalue()
        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"

    def test_an_unknown_chatbot_id_is_refused(self, db, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_args: "")

        with pytest.raises(CommandError, match="No chatbot with id"):
            call_command("scrub_participant_data", "some-team", "123456789")

    def test_a_chatbot_in_another_team_is_refused(self, chatbot, monkeypatch):
        """A mistyped id names a real chatbot somewhere else; the team slug catches that."""
        monkeypatch.setattr("builtins.input", lambda *_args: "")

        with pytest.raises(CommandError, match="in team 'not-this-team'"):
            call_command("scrub_participant_data", "not-this-team", str(chatbot["experiment"].id))

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"
        assert _participant_data(chatbot).data == {"name": SECRET, "age": 7}


class TestFilterArgumentParsing:
    """Reading the query string the operator pasted."""

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("f_participant=bob&op_participant=contains", id="bare-query-string"),
            pytest.param("?f_participant=bob&op_participant=contains", id="leading-question-mark"),
            pytest.param(
                "http://localhost:8000/a/team/chatbots/36/?f_participant=bob&op_participant=contains",
                id="whole-url",
            ),
        ],
    )
    def test_equivalent_forms_produce_the_same_filter(self, raw):
        filters = parse_filter_query_string(raw).filters

        assert len(filters) == 1
        assert (filters[0].column, filters[0].value) == ("participant", "bob")

    @pytest.mark.parametrize(
        "raw",
        [pytest.param("", id="empty"), pytest.param("   ", id="whitespace"), pytest.param(None, id="none")],
    )
    def test_an_empty_string_means_no_filter(self, raw):
        assert parse_filter_query_string(raw).filters == []


@pytest.mark.django_db()
class TestFilterArgumentGuard:
    """A filter that would not narrow the scrub is refused rather than silently widened."""

    @pytest.mark.parametrize(
        ("filter_query", "message"),
        [
            pytest.param("page=2&sort=name", "no usable filters", id="unrelated-params"),
            pytest.param("http://localhost:8000/a/team/chatbots/36/", "no usable filters", id="url-with-no-query"),
            pytest.param(
                "filter_0_column=participant&filter_0_operator=contains&filter_0_value=bob",
                "no usable filters",
                id="legacy-format",
            ),
            pytest.param("f_bogus=x&op_bogus=contains", "not a session filter", id="unknown-column"),
            pytest.param("f_participant=x&op_participant=is", "does not support", id="unsupported-operator"),
            pytest.param("f_last_message=yesterday&op_last_message=on", "matched every session", id="unparseable-date"),
            pytest.param(
                f"f_participant=user1&op_participant={Operators.CONTAINS}",
                "pass an empty --filter",
                id="genuinely-matches-everything",
            ),
        ],
    )
    def test_a_filter_that_would_not_narrow_the_scrub_is_refused(self, chatbot, monkeypatch, filter_query, message):
        """An unknown column, operator or value narrows nothing, which would scrub everyone."""
        with pytest.raises(CommandError, match=message):
            _scrub(chatbot["experiment"], monkeypatch, answers=[], filter_query=filter_query)

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"

    def test_each_filter_must_narrow_on_its_own(self, chatbot, monkeypatch):
        """A no-op filter hiding behind a real one would scrub more than the operator confirmed."""
        _other_participant(chatbot["experiment"], identifier="user2@example.com")
        filter_query = (
            f"f_participant=user1&op_participant={Operators.CONTAINS}"
            f"&f_last_message=yesterday&op_last_message={Operators.ON}"
        )

        with pytest.raises(CommandError, match='"last_message on yesterday" matched every session'):
            _scrub(chatbot["experiment"], monkeypatch, answers=[], filter_query=filter_query)

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"


@pytest.mark.django_db()
class TestSafety:
    """The properties that make an irreversible command usable."""

    def test_dry_run_changes_nothing_but_reports_the_counts(self, chatbot, monkeypatch):
        """The answers list holds no confirmation, so a prompt would raise StopIteration."""
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, answers=["all", "", "[REDACTED]"], stdout=output, dry_run=True)

        printed = output.getvalue()
        assert "nothing was written" in printed
        assert _totals(printed)["messages"] == 1

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == f"Hi, {SECRET} here"
        assert ExperimentSession.objects.get(id=chatbot["session"].id).state == {"last_caller": SECRET, "age": 7}
        assert Participant.objects.get(id=chatbot["participant"].id).name == SECRET
        assert _participant_data(chatbot).data == {"name": SECRET, "age": 7}

    def test_running_twice_changes_nothing_the_second_time(self, chatbot, monkeypatch):
        _scrub(chatbot["experiment"], monkeypatch)
        output = StringIO()

        _scrub(chatbot["experiment"], monkeypatch, stdout=output)

        printed = output.getvalue()
        assert _totals(printed) == dict.fromkeys(SURFACES, 0)
        assert _participant_data(chatbot).data == {"name": "[REDACTED]", "age": ""}

    def test_the_run_is_logged_without_any_values(self, chatbot, monkeypatch, caplog):
        """A deletion request has to be evidenced afterwards, and the log is all that survives."""
        with caplog.at_level(logging.INFO, logger=COMMAND):
            _scrub(chatbot["experiment"], monkeypatch)

        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "scrub_participant_data starting" in logged
        assert f"experiment={chatbot['experiment'].id}" in logged
        assert "keys=['age', 'name']" in logged
        assert "scrub_participant_data finished" in logged
        assert SECRET not in logged

    def test_an_interrupted_run_is_safely_re_runnable(self, chatbot, monkeypatch):
        """Participant data is written last, so a crash leaves the search terms readable.

        Without that ordering a half-finished run would have nothing left to hunt for on the
        surfaces it never reached.
        """

        def interrupt(**_kwargs):
            raise RuntimeError("interrupted mid-scrub")

        monkeypatch.setattr(f"{COMMAND}._scrub_participant_data", interrupt)

        with pytest.raises(RuntimeError, match="interrupted mid-scrub"):
            _scrub(chatbot["experiment"], monkeypatch)

        assert _participant_data(chatbot).data == {"name": SECRET, "age": 7}
        assert ChatMessage.objects.get(id=chatbot["message"].id).content == "Hi, [REDACTED] here"

        monkeypatch.undo()
        _scrub(chatbot["experiment"], monkeypatch)

        assert _participant_data(chatbot).data == {"name": "[REDACTED]", "age": ""}
        assert not ChatMessage.objects.filter(content__icontains=SECRET).exists()

    def test_a_replacement_containing_the_original_is_not_reapplied(self, chatbot, monkeypatch):
        """Replacing "John Smith" with "John Smith (removed)" must not compound on a rerun."""
        _scrub(chatbot["experiment"], monkeypatch, answers=["2", f"{SECRET} (removed)", "SCRUB"])
        first = ChatMessage.objects.get(id=chatbot["message"].id).content

        _scrub(chatbot["experiment"], monkeypatch, answers=["2", f"{SECRET} (removed)", "SCRUB"])

        assert ChatMessage.objects.get(id=chatbot["message"].id).content == first
