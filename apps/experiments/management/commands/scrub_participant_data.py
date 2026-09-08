"""Permanently overwrite chosen participant-data values across one chatbot's records.

Two primitives do all the work: a value-based text scan, which searches free text for the
participant's own values, and a key-based JSON replacement, which rewrites a selected key's
value at any depth regardless of its type. The key-based pass is what clears short and
non-string values, which are too ambiguous to search for in prose.

Reached: chat messages, chat names, session state, pipeline chat history, traces, evaluation
messages, evaluator results, the throwaway sessions bot generation ran the participant's
messages through, participant names and participant data.

Out of reach: copies held by external trace providers, evaluation messages whose session has
been deleted (``session`` is SET_NULL and there is no participant to fall back on), and
anything already exported to a file or a downstream system. Participant identifiers are left
alone deliberately: they are the participant's identity and unique key.

Nothing is wrapped in a transaction, so each batch commits on its own. A participant's data
row is written after every other surface of theirs, which makes an interrupted run safely
re-runnable: the values still to be hunted for are read from that row.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Prefetch, Q, QuerySet
from django.db.models.functions import Coalesce
from django.http import QueryDict

from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage
from apps.evaluations.models import EvaluationMessage, EvaluationResult
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.pipelines.models import PipelineChatMessages
from apps.trace.models import Trace
from apps.web.dynamic_filters.datastructures import FilterParams

logger = logging.getLogger(__name__)

MIN_SCANNABLE_VALUE_LENGTH = 4
CHUNK_SIZE = 500
CONFIRMATION_PHRASE = "SCRUB"

# The annotation every surface carries to say which participant's plan applies to a row.
OWNER = "scrub_owner"

# Keys that carry the *structure* of the JSON surfaces below, rather than participant data.
# Key-based replacement matches at any depth, so selecting a participant-data key that happens
# to share one of these names also blanks the conversation content stored under it.
STRUCTURAL_JSON_KEYS = frozenset(
    {
        "content",
        "role",
        "message_type",
        "summary",
        "trace_id",
        "trace_url",
        "session_id",
        "experiment_id",
        "created_mode",
        # EvaluationResult.output wraps a whole copy of the message, so its field names are
        # structural keys there too.
        "message",
        "generated_response",
        "result",
        "input",
        "output",
        "context",
        "history",
        "metadata",
        "participant_data",
        "session_state",
    }
)


def value_is_scannable(value: object) -> bool:
    """Whether a value is distinctive enough to search for in free text.

    Short values ("7", "Bob") match far too much unrelated text to be worth scanning for;
    the key-based pass is what removes those.
    """
    return isinstance(value, str) and len(value.strip()) >= MIN_SCANNABLE_VALUE_LENGTH


@dataclass
class ScrubPlan:
    """What to replace, for one participant.

    ``replacements`` maps a participant-data key to the string that replaces it. ``originals``
    holds that participant's current values, which is what the text scan searches for. A key
    with no original still has its value replaced wherever the key appears in JSON.
    """

    replacements: dict[str, str]
    originals: dict[str, object]

    @cached_property
    def text_rule(self) -> tuple[re.Pattern, list[str]] | None:
        """One alternation over every search term, plus the replacement for each alternative.

        One pattern rather than one per value, so one key's replacement cannot then be
        re-matched by another key's rule. Values are stripped, so a stored ``" John "`` still
        matches ``John`` in prose. Longest term first, so a value that is a prefix of another
        cannot consume the longer match ("John" against "John Smith").
        """
        pairs = sorted(
            (
                (str(value).strip(), self.replacements[key])
                for key, value in self.originals.items()
                if key in self.replacements and value_is_scannable(value)
            ),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        if not pairs:
            return None
        alternatives = "|".join(f"({re.escape(original)})" for original, _ in pairs)
        pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
        return pattern, [replacement for _, replacement in pairs]


@dataclass
class Scope:
    """The rows one chunk of participants can reach."""

    sessions: QuerySet
    participant_ids: list[int]
    experiment_ids: list[int]


def scrub_text(text: str, plan: ScrubPlan) -> str:
    """Replace every occurrence of the participant's values in ``text``."""
    if plan.text_rule is None:
        return text
    pattern, replacements = plan.text_rule
    # A function replacement, so backslashes in the replacement string stay literal instead of
    # being read as group references. Only one alternative can match, so its group number
    # indexes the replacement list.
    return pattern.sub(lambda match: replacements[match.lastindex - 1], text)


def scrub_json(value, plan: ScrubPlan):
    """Replace selected keys' values, and text-scan every string leaf, at any depth."""
    if isinstance(value, dict):
        return {
            key: plan.replacements[key] if key in plan.replacements else scrub_json(item, plan)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub_json(item, plan) for item in value]
    if isinstance(value, str):
        return scrub_text(value, plan)
    return value


def _scrub_fields(row, plan: ScrubPlan, text_fields: tuple[str, ...], json_fields: tuple[str, ...]) -> bool:
    """Rewrite the named fields in place; return whether anything actually changed."""
    changed = False
    for name in text_fields:
        current = getattr(row, name) or ""
        scrubbed = scrub_text(current, plan)
        if scrubbed != current:
            setattr(row, name, scrubbed)
            changed = True
    for name in json_fields:
        current = getattr(row, name)
        scrubbed = scrub_json(current, plan)
        if scrubbed != current:
            setattr(row, name, scrubbed)
            changed = True
    return changed


def _apply(
    queryset,
    plans: dict[int, ScrubPlan],
    *,
    dry_run: bool,
    text_fields: tuple[str, ...] = (),
    json_fields: tuple[str, ...] = (),
) -> int:
    """Stream ``queryset``, scrub each row with its owner's plan, and write back what changed.

    ``plans`` is keyed by whatever the ``OWNER`` annotation yields, so one query serves a whole
    chunk of participants rather than one query per participant. A row whose owner is not in
    ``plans`` is left alone: scrubbing it would mean using someone else's search terms.
    """
    model = queryset.model
    fields = [*text_fields, *json_fields]
    changed = 0
    batch = []
    # only(): a message history is unbounded, and no other column is read or written here.
    for row in queryset.only(*fields).iterator(chunk_size=CHUNK_SIZE):
        plan = plans.get(getattr(row, OWNER))
        if plan is None:
            continue
        if _scrub_fields(row=row, plan=plan, text_fields=text_fields, json_fields=json_fields):
            batch.append(row)
        if len(batch) >= CHUNK_SIZE:
            if not dry_run:
                model.objects.bulk_update(batch, fields)
            changed += len(batch)
            batch = []
    if batch:
        if not dry_run:
            model.objects.bulk_update(batch, fields)
        changed += len(batch)
    return changed


def _owned_by(queryset, owner):
    """Annotate which participant owns each row, and order for a stable stream."""
    return queryset.annotate(**{OWNER: owner}).order_by("id")


def _scrub_messages(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Rewrite the text and translations of every message in the eligible sessions."""
    queryset = _owned_by(
        ChatMessage.objects.filter(chat__experiment_session__in=scope.sessions),
        F("chat__experiment_session__participant_id"),
    )
    return _apply(
        queryset=queryset,
        plans=plans,
        dry_run=dry_run,
        text_fields=("content", "summary"),
        json_fields=("translations",),
    )


def _scrub_chat_names(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Text-scan ``Chat.name``, which is "<identifier> - <channel>".

    Only the text scan can reach it, and identifiers are deliberately preserved, so the name
    changes only when the identifier is also stored as one of the participant's data values.
    """
    if all(plan.text_rule is None for plan in plans.values()):
        return 0
    queryset = _owned_by(
        Chat.objects.filter(experiment_session__in=scope.sessions), F("experiment_session__participant_id")
    )
    return _apply(queryset=queryset, plans=plans, dry_run=dry_run, text_fields=("name",))


def _scrub_session_state(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Rewrite the stored state of every eligible session."""
    return _apply(
        queryset=_owned_by(scope.sessions, F("participant_id")), plans=plans, dry_run=dry_run, json_fields=("state",)
    )


def _scrub_pipeline_history(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Rewrite the pipeline's own copy of the conversation.

    An LLM node whose history mode is not "global" writes each turn to ``PipelineChatMessages``
    as well as to ``ChatMessage``, so the transcript exists twice.
    """
    queryset = _owned_by(
        PipelineChatMessages.objects.filter(chat_history__session__in=scope.sessions),
        F("chat_history__session__participant_id"),
    )
    return _apply(
        queryset=queryset, plans=plans, dry_run=dry_run, text_fields=("human_message", "ai_message", "summary")
    )


def _scrub_traces(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Traces on the eligible sessions, plus these participants' traces whose session FK was nulled."""
    queryset = _owned_by(
        Trace.objects.filter(
            Q(session__in=scope.sessions)
            | Q(
                session__isnull=True,
                participant_id__in=scope.participant_ids,
                experiment_id__in=scope.experiment_ids,
            )
        ),
        # An orphaned trace has only its own participant FK left to identify it by.
        Coalesce(F("session__participant_id"), F("participant_id")),
    )
    return _apply(
        queryset=queryset,
        plans=plans,
        dry_run=dry_run,
        text_fields=("error",),
        json_fields=("participant_data", "participant_data_diff", "session_state", "trace_metadata"),
    )


def _scrub_eval_messages(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Rewrite the evaluation messages captured from the eligible sessions."""
    queryset = _owned_by(EvaluationMessage.objects.filter(session__in=scope.sessions), F("session__participant_id"))
    return _apply(
        queryset=queryset,
        plans=plans,
        dry_run=dry_run,
        json_fields=("input", "output", "context", "history", "participant_data", "session_state", "metadata"),
    )


def _scrub_eval_results(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Rewrite the copy of the message that every evaluator result embeds.

    ``EvaluationResult.output`` holds ``EvaluationMessage.as_result_dict()`` under "message",
    so each evaluator run against a message keeps a second copy of the participant's data and
    conversation, alongside whatever a judge quoted back. Reached through the message, because
    ``EvaluationResult.session`` is the throwaway session the bot generation ran in.
    """
    queryset = _owned_by(
        EvaluationResult.objects.filter(message__session__in=scope.sessions), F("message__session__participant_id")
    )
    return _apply(queryset=queryset, plans=plans, dry_run=dry_run, json_fields=("output",))


def _generated_session_plans(scope: Scope, plans: dict[int, ScrubPlan]) -> dict[int, ScrubPlan]:
    """Map each throwaway generation session to the plan of the participant it copied.

    Bot generation copies the participant's session state and history into a session owned by
    the synthetic "evaluations" participant, which the match deliberately leaves out. The
    evaluator result the generation fed is the only link back.
    """
    rows = EvaluationResult.objects.filter(message__session__in=scope.sessions, session__isnull=False).values_list(
        "session_id", "message__session__participant_id"
    )
    return {session_id: plans[owner] for session_id, owner in rows if owner in plans}


def _scrub_generated_sessions(scope: Scope, plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Scrub the throwaway sessions bot generation ran the eligible messages through.

    Keyed by session rather than by participant: these sessions all belong to the same
    synthetic participant, so only the session id says whose data they hold.
    """
    plans_by_session = _generated_session_plans(scope=scope, plans=plans)
    if not plans_by_session:
        return 0
    session_ids = list(plans_by_session)
    passes = (
        (ExperimentSession.objects.filter(id__in=session_ids), F("id"), (), ("state",)),
        (
            ChatMessage.objects.filter(chat__experiment_session__id__in=session_ids),
            F("chat__experiment_session__id"),
            ("content", "summary"),
            ("translations",),
        ),
        (
            PipelineChatMessages.objects.filter(chat_history__session_id__in=session_ids),
            F("chat_history__session_id"),
            ("human_message", "ai_message", "summary"),
            (),
        ),
        (
            Trace.objects.filter(session_id__in=session_ids),
            F("session_id"),
            ("error",),
            ("participant_data", "participant_data_diff", "session_state", "trace_metadata"),
        ),
    )
    changed = 0
    for queryset, owner, text_fields, json_fields in passes:
        changed += _apply(
            queryset=_owned_by(queryset, owner),
            plans=plans_by_session,
            dry_run=dry_run,
            text_fields=text_fields,
            json_fields=json_fields,
        )
    return changed


# Every table one run rewrites, in the order they are scrubbed and reported.
SURFACE_SCRUBBERS = (
    ("messages", _scrub_messages),
    ("chat_names", _scrub_chat_names),
    ("session_state", _scrub_session_state),
    ("pipeline_history", _scrub_pipeline_history),
    ("traces", _scrub_traces),
    ("evaluation_messages", _scrub_eval_messages),
    ("evaluation_results", _scrub_eval_results),
    ("evaluation_sessions", _scrub_generated_sessions),
)

SURFACES = (*(label for label, _ in SURFACE_SCRUBBERS), "participant_names", "participant_data")


def _scrub_participant_names(participants: list[Participant], plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Rewrite ``Participant.name``, which holds a copy of this participant's "name" value.

    Replaced wholesale only when the participant actually holds a selected "name":
    ``replacements`` is the operator's one global selection while ``originals`` is per
    participant, and ``Participant.name`` is team-scoped.
    """
    changed = []
    for participant in participants:
        plan = plans[participant.id]
        original = participant.name
        if not original:
            continue
        if "name" in plan.replacements and "name" in plan.originals:
            scrubbed = plan.replacements["name"]
        else:
            scrubbed = scrub_text(original, plan)
        if scrubbed != original:
            participant.name = scrubbed
            changed.append(participant)
    if changed and not dry_run:
        Participant.objects.bulk_update(changed, ["name"])
    return len(changed)


def _scrub_participant_data(data_rows: dict[int, ParticipantData], plans: dict[int, ScrubPlan], dry_run: bool) -> int:
    """Rewrite the participant-data rows themselves, the last surface to be touched."""
    changed = []
    for participant_id, data_row in data_rows.items():
        scrubbed = scrub_json(data_row.data, plans[participant_id])
        if scrubbed != data_row.data:
            data_row.data = scrubbed
            changed.append(data_row)
    if changed and not dry_run:
        ParticipantData.objects.bulk_update(changed, ["data"])
    return len(changed)


def _version_family_ids(working: Experiment) -> list[int]:
    """Every id in the chatbot's version family, archived versions included.

    ``Experiment.version_family_ids`` walks the default related manager, which drops archived
    versions — and an archived version's orphaned traces still hold participant data.
    """
    versions = Experiment.objects.get_all().filter(working_version_id=working.id).values_list("id", flat=True)
    return [working.id, *versions]


def _participant_chunks(working: Experiment, participant_ids: list[int]):
    """Yield the participants CHUNK_SIZE at a time, each with its participant-data row.

    ``ParticipantData.data`` is decrypted on read, so the rows are fetched a chunk at a time
    rather than all at once.
    """
    for start in range(0, len(participant_ids), CHUNK_SIZE):
        yield list(
            Participant.objects.filter(team=working.team, id__in=participant_ids[start : start + CHUNK_SIZE])
            .order_by("id")
            .prefetch_related(
                Prefetch(
                    "data_set",
                    queryset=ParticipantData.objects.for_experiment(working).order_by("id"),
                    to_attr="scrub_data",
                )
            )
        )


def resolve_selection(answer: str, keys: list[str]) -> list[str]:
    """Turn a typed answer ("all", or numbers) into the list of keys it names."""
    answer = answer.strip()
    if answer.lower() == "all":
        return keys
    selected = []
    for token in (part.strip() for part in answer.split(",")):
        if not token:
            continue
        if not token.isdigit() or not 1 <= int(token) <= len(keys):
            raise CommandError(f"{token!r} is not one of the listed numbers; nothing was written.")
        key = keys[int(token) - 1]
        if key not in selected:
            selected.append(key)
    if not selected:
        raise CommandError("No keys selected; nothing was written.")
    return selected


def parse_filter_query_string(raw: str) -> FilterParams:
    """Parse a filter query string, tolerating a whole URL or a leading "?"."""
    raw = (raw or "").strip()
    if not raw:
        return FilterParams()
    if "://" in raw:
        raw = urlparse(raw).query
    return FilterParams(QueryDict(raw.lstrip("?")))


class Command(BaseCommand):
    """Interactively scrub a chosen set of participant-data values from one chatbot."""

    help = (
        "Permanently overwrite participant-data values across a chatbot's messages, chat names, "
        "session state, pipeline chat history, traces, evaluation messages, evaluator results, "
        "participant names and participant data. Keys and their replacements are chosen at the "
        "prompt. Cannot be undone."
    )

    def add_arguments(self, parser):
        parser.add_argument("team_slug", help="The slug of the team the chatbot belongs to, as it appears in its URL")
        parser.add_argument("experiment_id", type=int, help="The chatbot's numeric ID, as it appears in its URL")
        parser.add_argument(
            "--filter",
            default="",
            help=(
                "Session filter query string copied from the Sessions tab URL (a whole URL is "
                "also accepted). It selects participants: every session those participants had "
                "with this chatbot is scrubbed. Empty means every participant of the chatbot."
            ),
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing")

    def handle(self, *args, **options):
        """Resolve the targets, ask what to replace, confirm, then scrub."""
        dry_run = options["dry_run"]
        experiment = self._get_experiment(options["team_slug"], options["experiment_id"])
        working = experiment.get_working_version()
        filter_params = self._parse_filter(options["filter"])

        matched_count, participant_ids = self._resolve_matched(working, filter_params)
        if not participant_ids:
            self.stdout.write(self.style.WARNING("The filter matched no sessions. Nothing to do."))
            return

        experiment_ids = _version_family_ids(working)
        sessions = ExperimentSession.objects.filter(
            team=working.team, experiment_id__in=experiment_ids, participant_id__in=participant_ids
        )
        key_counts, unscannable_counts = self._discover_keys(working, participant_ids)

        self.stdout.write(f'Chatbot: "{working.name}" (id={working.id}, team={working.team.slug})')
        self.stdout.write(f"Sessions matched by the filter: {matched_count}")
        self.stdout.write(f"Participants: {len(participant_ids)}")
        self.stdout.write(f"Sessions to scrub (every session these participants had here): {sessions.count()}")

        if not key_counts:
            self.stdout.write(self.style.WARNING("These participants hold no participant data. Nothing to scrub."))
            return

        selected_keys = self._prompt_for_keys(key_counts, unscannable_counts)
        replacements = self._prompt_for_replacements(selected_keys)

        self._report_plan(replacements, key_counts, unscannable_counts, dry_run=dry_run)

        if not dry_run:
            self._confirm()

        logger.info(
            "scrub_participant_data starting: experiment=%s team=%s participants=%s filter=%r keys=%s dry_run=%s",
            working.id,
            working.team.slug,
            len(participant_ids),
            options["filter"],
            sorted(replacements),
            dry_run,
        )
        totals = self._scrub(
            working=working,
            participant_ids=participant_ids,
            experiment_ids=experiment_ids,
            replacements=replacements,
            dry_run=dry_run,
        )
        logger.info(
            "scrub_participant_data finished: experiment=%s dry_run=%s totals=%s",
            working.id,
            dry_run,
            dict(totals),
        )
        self._report_totals(totals, dry_run=dry_run)

    def _get_experiment(self, team_slug: str, experiment_id: int) -> Experiment:
        """Load the chatbot by team and id, including an archived one.

        The id alone is a bare number, and a mistyped one names a real chatbot somewhere else.
        The team is part of the lookup, so a typo finds nothing rather than a stranger's chatbot.
        """
        experiment = Experiment.objects.get_all().filter(id=experiment_id, team__slug=team_slug).first()
        if experiment is None:
            raise CommandError(f"No chatbot with id={experiment_id} in team {team_slug!r}")
        return experiment

    def _parse_filter(self, raw: str) -> FilterParams:
        """Parse --filter, refusing anything the session filter would not actually apply."""
        filter_params = parse_filter_query_string(raw)
        if raw.strip() and not filter_params.filters:
            raise CommandError(
                f"The filter {raw!r} produced no usable filters. Pass an empty --filter to mean "
                "every session, rather than risking a scrub that is wider than you intended."
            )
        self._validate_filter(filter_params)
        return filter_params

    def _validate_filter(self, filter_params: FilterParams) -> None:
        """Refuse a column or operator that ExperimentSessionFilter would silently ignore.

        ``MultiColumnFilter.apply`` walks only its own declared columns, and each column only
        its own ``apply_<operator>`` methods, so an unknown column or operator narrows nothing
        while still looking like a filter. Here that means scrubbing the whole chatbot.
        """
        known = {column.query_param: column for column in ExperimentSessionFilter.filters}
        for filter_data in filter_params.filters:
            column = known.get(filter_data.column)
            if column is None:
                raise CommandError(
                    f'"{filter_data.column}" is not a session filter column, so it would narrow '
                    f"nothing. Available columns: {', '.join(sorted(known))}."
                )
            if filter_data.operator not in column.operators:
                raise CommandError(
                    f'The "{filter_data.column}" filter does not support the operator '
                    f'"{filter_data.operator}", so it would narrow nothing. Supported: '
                    f"{', '.join(column.operators)}."
                )

    def _resolve_matched(self, working: Experiment, filter_params: FilterParams) -> tuple[int, list[int]]:
        """Apply the filter and return how many sessions matched, and their participant ids.

        Sessions owned by the synthetic evaluations participant are left out. Bot generation
        runs every evaluation message through one, so together they hold copies of whatever
        the team's datasets contain. The ones that copied a matched participant are reached
        through their evaluator result instead.
        """
        base = ExperimentSession.objects.get_table_queryset(working.team, working.id).exclude(
            participant__platform=ChannelPlatform.EVALUATIONS
        )
        if not filter_params.filters:
            queryset = base
        else:
            self._refuse_filters_that_narrow_nothing(base, filter_params)
            queryset = ExperimentSessionFilter().apply(base, filter_params=filter_params)
        matched_count = queryset.order_by().count()
        participant_ids = queryset.order_by().values_list("participant_id", flat=True).distinct()
        return matched_count, sorted(participant_ids)

    def _refuse_filters_that_narrow_nothing(self, base: QuerySet, filter_params: FilterParams) -> None:
        """Apply each filter on its own and refuse any that matches every session.

        A filter can narrow nothing without erroring — TimestampFilter swallows an unparseable
        date, a choice filter drops values it cannot parse — and matching everything is the
        only signal. Each is checked alone, because a real filter beside it would hide the
        no-op, and the scrub would reach more participants than the operator confirmed.
        """
        total = base.order_by().count()
        if not total:
            return
        session_filter = ExperimentSessionFilter()
        for filter_data in filter_params.filters:
            alone = session_filter.apply(base, filter_params=FilterParams(column_filters=[filter_data]))
            if alone.order_by().count() == total:
                raise CommandError(
                    f'The filter "{filter_data.column} {filter_data.operator} {filter_data.value}" matched '
                    "every session of this chatbot, so it narrowed nothing. If you really do mean every "
                    "participant, pass an empty --filter."
                )

    def _discover_keys(self, working, participant_ids) -> tuple[Counter, Counter]:
        """Top-level participant-data keys, with counts only — never a value.

        Decrypting in-process is the only option: `ParticipantData.data` is encrypted at
        rest, so no SQL can reach inside it.
        """
        key_counts: Counter = Counter()
        unscannable_counts: Counter = Counter()
        rows = ParticipantData.objects.for_experiment(working).filter(participant_id__in=participant_ids)
        for row in rows.iterator(chunk_size=CHUNK_SIZE):
            for key, value in (row.data or {}).items():
                key_counts[key] += 1
                if not value_is_scannable(value):
                    unscannable_counts[key] += 1
        return key_counts, unscannable_counts

    def _prompt_for_keys(self, key_counts: Counter, unscannable_counts: Counter) -> list[str]:
        """Offer the discovered keys and read back a selection."""
        keys = sorted(key_counts)
        self.stdout.write("\nParticipant-data keys (participant counts only, never values):")
        for number, key in enumerate(keys, start=1):
            note = ""
            if unscannable_counts[key]:
                note = f" — {unscannable_counts[key]} too short/non-text to scan for in prose"
            self.stdout.write(f"  {number}. {key}: {key_counts[key]} participant(s){note}")
        answer = self._ask('\nKeys to scrub (comma-separated numbers, or "all"): ')
        return resolve_selection(answer=answer, keys=keys)

    def _prompt_for_replacements(self, keys: list[str]) -> dict[str, str]:
        """Ask for the replacement string for each selected key."""
        self.stdout.write("\nEnter a replacement for each key. An empty answer clears the value.")
        return {key: self._ask(f'  Replacement for "{key}": ') for key in keys}

    def _report_plan(
        self, replacements: dict[str, str], key_counts: Counter, unscannable_counts: Counter, *, dry_run: bool
    ) -> None:
        """Print the replacements and, for a real run, what is about to be destroyed."""
        self.stdout.write("\nReplacements:")
        for key in sorted(replacements):
            shown = replacements[key] if replacements[key] else "(cleared)"
            self.stdout.write(f"  {key} -> {shown}{self._scan_note(key, key_counts, unscannable_counts)}")
        self.stdout.write(
            "\nA selected key is cleared wherever it appears in the JSON being rewritten, including "
            "for in-scope participants who never held it. The counts above are only how many "
            "participants hold the key today."
        )
        self._warn_about_structural_keys(replacements)
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN — nothing will be written."))
            return
        self.stdout.write(
            self.style.ERROR(
                "\nThis permanently overwrites messages, chat names, session state, pipeline chat "
                "history, traces, evaluation messages, evaluator results, evaluation sessions, "
                "participant names and participant data. It cannot be undone and there is no backup."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "Out of reach from here: copies held by external trace providers (Langfuse and "
                "friends); evaluation messages whose session has been deleted, which have no "
                "participant left to find them by; and anything already exported to a file or a "
                "downstream system.\n"
                "Participant names are team-scoped, so clearing a name is visible in this team's "
                "other chatbots as well. Participant identifiers are left alone: they are the "
                "participant's identity and unique key, so they survive the scrub."
            )
        )

    def _scan_note(self, key: str, key_counts: Counter, unscannable_counts: Counter) -> str:
        """How much of this key's population the text scan will actually hunt for."""
        unscannable = unscannable_counts[key]
        if not unscannable:
            return ""
        held = key_counts[key]
        if unscannable == held:
            return " — cleared by key only, not text-scanned"
        return f" — text-scanned for {held - unscannable} of {held} participants, key-only for the rest"

    def _warn_about_structural_keys(self, replacements: dict[str, str]) -> None:
        """Say so when a selected key also names a field of the JSON being rewritten."""
        collisions = sorted(set(replacements) & STRUCTURAL_JSON_KEYS)
        if collisions:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{', '.join(collisions)} is also used as a structural key inside stored "
                    "message translations, evaluation records and trace metadata. Those fields "
                    "will be overwritten too, not just the participant's own value."
                )
            )

    def _confirm(self) -> None:
        """Require the confirmation phrase to be typed, or abort before writing anything."""
        answer = self._ask(f'Type "{CONFIRMATION_PHRASE}" to continue, anything else to abort: ')
        if answer.strip() != CONFIRMATION_PHRASE:
            raise CommandError("Aborted; nothing was written.")

    def _ask(self, prompt: str) -> str:
        """Read one answer, failing cleanly when there is no one at the keyboard."""
        try:
            return input(prompt)
        except EOFError:
            raise CommandError("This command has to be answered interactively; nothing was written.") from None

    def _scrub(self, working, participant_ids, experiment_ids, replacements, *, dry_run: bool) -> Counter:
        """Scrub a chunk of participants at a time, and report the rows changed per surface.

        Each participant needs its own plan: the values to search for in free text are read
        from that participant's own data. Rows are fetched for a whole chunk and matched back
        to their owner, so the query count follows the number of chunks rather than the number
        of participants. Participant data is written last, because it is where those values
        were read from, so an interrupted run can still find them.
        """
        totals: Counter = Counter()
        done = 0
        for chunk in _participant_chunks(working=working, participant_ids=participant_ids):
            plans = {}
            data_rows = {}
            for participant in chunk:
                data_row = participant.scrub_data[0] if participant.scrub_data else None
                if data_row is not None:
                    data_rows[participant.id] = data_row
                plans[participant.id] = ScrubPlan(
                    replacements=replacements, originals=(data_row.data or {}) if data_row else {}
                )
            scope = Scope(
                sessions=ExperimentSession.objects.filter(
                    team=working.team, experiment_id__in=experiment_ids, participant_id__in=list(plans)
                ),
                participant_ids=list(plans),
                experiment_ids=experiment_ids,
            )
            for label, scrub in SURFACE_SCRUBBERS:
                totals[label] += scrub(scope, plans, dry_run)
            totals["participant_names"] += _scrub_participant_names(participants=chunk, plans=plans, dry_run=dry_run)
            totals["participant_data"] += _scrub_participant_data(data_rows=data_rows, plans=plans, dry_run=dry_run)
            done += len(chunk)
            logger.info(
                "scrub_participant_data progress: experiment=%s participants=%s/%s totals=%s",
                working.id,
                done,
                len(participant_ids),
                dict(totals),
            )
        return totals

    def _report_totals(self, totals: Counter, *, dry_run: bool) -> None:
        """Print the per-surface row counts."""
        verb = "Would change" if dry_run else "Changed"
        self.stdout.write(f"\n{verb}:")
        for label in SURFACES:
            self.stdout.write(f"  {label}: {totals[label]}")
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run complete; nothing was written."))
        else:
            self.stdout.write(self.style.SUCCESS("\nScrub complete."))
