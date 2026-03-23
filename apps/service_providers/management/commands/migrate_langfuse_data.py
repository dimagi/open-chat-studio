#!/usr/bin/env python
"""Django management command to migrate Langfuse traces between projects."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from langfuse import Langfuse
from langfuse.api.core.api_error import ApiError
from langfuse.api.resources.commons.types import Usage
from langfuse.api.resources.ingestion.types import (
    CreateEventBody,
    CreateGenerationBody,
    CreateSpanBody,
    IngestionEvent_EventCreate,
    IngestionEvent_GenerationCreate,
    IngestionEvent_ScoreCreate,
    IngestionEvent_SpanCreate,
    IngestionEvent_TraceCreate,
    ScoreBody,
    TraceBody,
)

from apps.service_providers.models import TraceProvider, TraceProviderType
from apps.teams.models import Team

DEFAULT_LANGFUSE_HOST = "https://cloud.langfuse.com"


@dataclasses.dataclass
class CheckpointState:
    migrated_ids: set
    checkpoint_file: str
    max_resume_timestamp: str | None = None
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def update(self, trace_id: str, trace_ts: str | None) -> None:
        """Thread-safe checkpoint update. Only advances resume timestamp forward."""
        with self.lock:
            self.migrated_ids.add(trace_id)
            if trace_ts and (self.max_resume_timestamp is None or trace_ts > self.max_resume_timestamp):
                self.max_resume_timestamp = trace_ts
            _save_checkpoint(self.checkpoint_file, self.migrated_ids, resume_from_timestamp=self.max_resume_timestamp)


@dataclasses.dataclass
class RetryConfig:
    max_retries: int = 4
    base_sleep: float = 0.5


def _is_rate_limited(exc: Exception) -> bool:
    """Check if an exception represents an HTTP 429 rate limit response."""
    return isinstance(exc, ApiError) and exc.status_code == 429


def _safe_isoformat(dt_obj):
    """Format datetime object to ISO 8601 string, handling None."""
    if dt_obj is None:
        return None
    if not isinstance(dt_obj, dt.datetime):
        if isinstance(dt_obj, str):
            try:
                dt.datetime.fromisoformat(dt_obj.replace("Z", "+00:00"))
                return dt_obj
            except ValueError:
                return None
        return None
    try:
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=dt.UTC)
        iso_str = dt_obj.isoformat(timespec="milliseconds")
        if iso_str.endswith("+00:00"):
            iso_str = iso_str[:-6] + "Z"
        return iso_str
    except Exception:
        return None


def _parse_datetime(datetime_str):
    """Parse an ISO 8601 datetime string into a timezone-aware datetime object."""
    if not datetime_str:
        return None
    try:
        if isinstance(datetime_str, str) and datetime_str.endswith("Z"):
            datetime_str = datetime_str[:-1] + "+00:00"
        dt_obj = dt.datetime.fromisoformat(datetime_str)
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=dt.UTC)
        return dt_obj
    except (ValueError, TypeError):
        return None


def _load_checkpoint(filepath: str) -> tuple[set, str | None]:
    """Load checkpoint state. Returns (migrated_ids, resume_from_timestamp)."""
    if not os.path.exists(filepath):
        return set(), None
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise CommandError(f"Invalid checkpoint file '{filepath}'. Use --reset-checkpoint to start fresh.") from e
    return set(data.get("migrated_ids", [])), data.get("resume_from_timestamp")


def _save_checkpoint(filepath: str, migrated_ids: set, resume_from_timestamp: str | None = None) -> None:
    """Write checkpoint state to file atomically to avoid corruption on interruption."""
    tmp_path = f"{filepath}.tmp"
    payload = {"migrated_ids": list(migrated_ids), "resume_from_timestamp": resume_from_timestamp}
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, filepath)


def _transform_trace_to_ingestion_batch(source_trace):
    """Transform a fetched TraceWithFullDetails into ingestion events for the batch endpoint."""

    ingestion_events = []
    preserved_trace_id = source_trace.id
    obs_id_map = {}

    trace_metadata = source_trace.metadata if isinstance(source_trace.metadata, dict) else {}
    trace_body = TraceBody(
        id=preserved_trace_id,
        timestamp=source_trace.timestamp,
        name=source_trace.name,
        user_id=source_trace.user_id,
        input=source_trace.input,
        output=source_trace.output,
        session_id=source_trace.session_id,
        release=source_trace.release,
        version=source_trace.version,
        metadata=trace_metadata or None,
        tags=source_trace.tags if source_trace.tags is not None else [],
        public=source_trace.public,
        environment=source_trace.environment,
    )
    event_timestamp_str = _safe_isoformat(dt.datetime.now(dt.UTC))
    if not event_timestamp_str:
        return []
    ingestion_events.append(
        IngestionEvent_TraceCreate(id=str(uuid.uuid4()), timestamp=event_timestamp_str, body=trace_body)
    )

    for source_obs in sorted(source_trace.observations, key=lambda o: o.start_time):
        new_obs_id = str(uuid.uuid4())
        obs_id_map[source_obs.id] = new_obs_id
        new_parent_observation_id = (
            obs_id_map.get(source_obs.parent_observation_id) if source_obs.parent_observation_id else None
        )
        obs_metadata = source_obs.metadata if isinstance(source_obs.metadata, dict) else {}

        model_params_mapped = None
        if isinstance(source_obs.model_parameters, dict):
            model_params_mapped = source_obs.model_parameters

        common_body_args = {
            "id": new_obs_id,
            "trace_id": preserved_trace_id,
            "name": source_obs.name,
            "start_time": source_obs.start_time,
            "metadata": obs_metadata or None,
            "input": source_obs.input,
            "output": source_obs.output,
            "level": source_obs.level,
            "status_message": source_obs.status_message,
            "parent_observation_id": new_parent_observation_id,
            "version": source_obs.version,
            "environment": source_obs.environment,
        }

        event_specific_timestamp = _safe_isoformat(dt.datetime.now(dt.UTC))
        if not event_specific_timestamp:
            continue

        try:
            if source_obs.type == "SPAN":
                event_body = CreateSpanBody(**common_body_args, end_time=source_obs.end_time)
                ingestion_event_type = IngestionEvent_SpanCreate
            elif source_obs.type == "EVENT":
                event_body = CreateEventBody(**common_body_args)
                ingestion_event_type = IngestionEvent_EventCreate
            elif source_obs.type == "GENERATION":
                usage_to_pass = None
                if isinstance(source_obs.usage, Usage):
                    usage_data = {
                        k: getattr(source_obs.usage, k, None)
                        for k in ["input", "output", "total", "unit", "input_cost", "output_cost", "total_cost"]
                    }
                    filtered = {k: v for k, v in usage_data.items() if v is not None}
                    if filtered:
                        usage_to_pass = Usage(**filtered)
                event_body = CreateGenerationBody(
                    **common_body_args,
                    end_time=source_obs.end_time,
                    completion_start_time=source_obs.completion_start_time,
                    model=source_obs.model,
                    model_parameters=model_params_mapped,
                    usage=usage_to_pass,
                    cost_details=source_obs.cost_details,
                    usage_details=source_obs.usage_details,
                    prompt_name=getattr(source_obs, "prompt_name", None),
                    prompt_version=getattr(source_obs, "prompt_version", None),
                )
                ingestion_event_type = IngestionEvent_GenerationCreate
            else:
                continue

            ingestion_events.append(
                ingestion_event_type(id=str(uuid.uuid4()), timestamp=event_specific_timestamp, body=event_body)
            )
        except Exception as e:
            raise ValueError(f"Failed to transform observation {source_obs.id} (type: {source_obs.type})") from e

    for source_score in source_trace.scores:
        new_observation_id = obs_id_map.get(source_score.observation_id) if source_score.observation_id else None
        score_metadata = source_score.metadata if isinstance(source_score.metadata, dict) else {}

        if source_score.data_type == "CATEGORICAL":
            if hasattr(source_score, "string_value") and isinstance(getattr(source_score, "string_value", None), str):
                score_body_value = source_score.string_value
            else:
                score_body_value = str(source_score.value) if source_score.value is not None else None
        elif source_score.data_type in ["NUMERIC", "BOOLEAN"]:
            score_body_value = source_score.value
        else:
            score_body_value = source_score.value

        if score_body_value is None:
            continue

        try:
            score_body = ScoreBody(
                id=str(uuid.uuid4()),
                trace_id=preserved_trace_id,
                name=source_score.name,
                value=score_body_value,
                source=source_score.source,
                comment=source_score.comment,
                observation_id=new_observation_id,
                timestamp=source_score.timestamp,
                config_id=source_score.config_id,
                metadata=score_metadata or None,
                data_type=source_score.data_type,
                environment=source_score.environment,
            )
            event_timestamp_str = _safe_isoformat(dt.datetime.now(dt.UTC))
            if not event_timestamp_str:
                continue
            ingestion_events.append(
                IngestionEvent_ScoreCreate(id=str(uuid.uuid4()), timestamp=event_timestamp_str, body=score_body)
            )
        except Exception as e:
            raise ValueError(f"Failed to transform score {source_score.id}") from e

    return ingestion_events


class Command(BaseCommand):
    help = "Migrate Langfuse traces from one project to another using team TraceProviders."

    def add_arguments(self, parser):
        parser.add_argument("team_slug", help="Slug of the team whose TraceProviders to use")

        flt = parser.add_argument_group("filters")
        flt.add_argument("--from-timestamp", metavar="ISO8601", help="Only migrate traces from this timestamp onward")
        flt.add_argument("--to-timestamp", metavar="ISO8601", help="Only migrate traces up to this timestamp")

        creds = parser.add_argument_group("credentials (for running without DB access)")
        creds.add_argument("--source-public-key", metavar="KEY", help="Source Langfuse public key")
        creds.add_argument("--source-secret-key", metavar="KEY", help="Source Langfuse secret key")
        creds.add_argument(
            "--source-host",
            metavar="URL",
            default=None,
            help=f"Source Langfuse host (default: {DEFAULT_LANGFUSE_HOST})",
        )
        creds.add_argument("--dest-public-key", metavar="KEY", help="Destination Langfuse public key")
        creds.add_argument("--dest-secret-key", metavar="KEY", help="Destination Langfuse secret key")
        creds.add_argument(
            "--dest-host",
            metavar="URL",
            default=None,
            help=f"Destination Langfuse host (default: {DEFAULT_LANGFUSE_HOST})",
        )
        creds.add_argument(
            "--print-command",
            action="store_true",
            help="Look up credentials from DB and print a runnable command with --source-*/--dest-* args, then exit",
        )

        adv = parser.add_argument_group("advanced")
        adv.add_argument(
            "--sleep-get", type=float, default=0.7, metavar="SEC", help="Seconds to sleep between trace GET requests"
        )
        adv.add_argument(
            "--sleep-batch", type=float, default=0.5, metavar="SEC", help="Seconds to sleep between batch pushes"
        )
        adv.add_argument("--max-retries", type=int, default=4, metavar="N", help="Maximum retries for failed requests")
        adv.add_argument(
            "--checkpoint-file",
            default=None,
            metavar="PATH",
            help="Checkpoint file path (default: .migration_checkpoint_<team_slug>.json)",
        )
        adv.add_argument("--reset-checkpoint", action="store_true", help="Delete checkpoint and start fresh")
        adv.add_argument("--dry-run", action="store_true", help="Fetch and transform but do not push to destination")
        adv.add_argument(
            "--workers",
            type=int,
            default=5,
            metavar="N",
            help="Number of concurrent workers for fetch/push (default: 5)",
        )

    def handle(self, *args, **options):
        team_slug = options["team_slug"]
        has_direct_creds = options["source_public_key"] and options["source_secret_key"]

        if has_direct_creds:
            source_config, dest_config = self._configs_from_cli_args(options)
        else:
            source_config, dest_config = self._configs_from_db(team_slug, options)
            if options["print_command"]:
                self._print_command(team_slug, source_config, dest_config, options)
                return

        checkpoint_file = options["checkpoint_file"] or f".migration_checkpoint_{team_slug}.json"
        if options["reset_checkpoint"] and os.path.exists(checkpoint_file):
            os.remove(checkpoint_file)
            self.stdout.write(f"Checkpoint file '{checkpoint_file}' deleted.\n")

        source_host = source_config.get("host", DEFAULT_LANGFUSE_HOST)
        dest_host = dest_config.get("host", DEFAULT_LANGFUSE_HOST)
        self.stdout.write(self.style.WARNING("\nWARNING: Migrates full trace data. PRESERVES ORIGINAL TRACE IDs."))
        self.stdout.write(self.style.WARNING("Ensure no ID collisions in the destination project!\n"))
        self.stdout.write(f"Source:      {source_host}")
        self.stdout.write(f"Destination: {dest_host}")
        if options["from_timestamp"]:
            self.stdout.write(f"From:        {options['from_timestamp']}")
        if options["to_timestamp"]:
            self.stdout.write(f"To:          {options['to_timestamp']}")
        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE("Mode:        DRY RUN"))
        self.stdout.write(f"Checkpoint:  {checkpoint_file}\n")

        confirmation = input("Proceed with trace migration? (yes/no): ").strip().lower()
        if confirmation != "yes":
            self.stdout.write("Migration cancelled.")
            return

        self._migrate_traces(
            source_config=source_config,
            dest_config=dest_config,
            from_timestamp_str=options["from_timestamp"],
            to_timestamp_str=options["to_timestamp"],
            retry_config=RetryConfig(
                max_retries=options["max_retries"],
                base_sleep=options["sleep_batch"],
            ),
            sleep_between_gets=options["sleep_get"],
            dry_run=options["dry_run"],
            checkpoint_file=checkpoint_file,
            workers=options["workers"],
        )

    def _configs_from_cli_args(self, options: dict) -> tuple[dict, dict]:
        """Build source/dest configs from direct CLI credential args."""
        if not options["dest_public_key"] or not options["dest_secret_key"]:
            raise CommandError(
                "When using --source-* credentials, --dest-public-key and --dest-secret-key are also required."
            )

        source_config = {
            "public_key": options["source_public_key"],
            "secret_key": options["source_secret_key"],
            "host": options["source_host"] or DEFAULT_LANGFUSE_HOST,
        }
        dest_config = {
            "public_key": options["dest_public_key"],
            "secret_key": options["dest_secret_key"],
            "host": options["dest_host"] or DEFAULT_LANGFUSE_HOST,
        }
        return source_config, dest_config

    def _configs_from_db(self, team_slug: str, options: dict) -> tuple[dict, dict]:
        """Look up source/dest configs from team TraceProviders in the DB."""
        try:
            team = Team.objects.get(slug=team_slug)
        except Team.DoesNotExist as e:
            raise CommandError(f"Team with slug '{team_slug}' not found.") from e

        providers = list(TraceProvider.objects.filter(team=team, type=TraceProviderType.langfuse).order_by("name"))
        if not providers:
            raise CommandError(f"No Langfuse TraceProviders found for team '{team_slug}'.")

        self.stdout.write(f"\nLangfuse TraceProviders for team '{team}':\n")
        for i, provider in enumerate(providers, 1):
            host = provider.config.get("host", DEFAULT_LANGFUSE_HOST)
            self.stdout.write(f"  [{i}] {provider.name}  ({host})")
        self.stdout.write("")

        source_default = self._find_provider_by_host(providers, "langfuse.openchatstudio.com")
        dest_default = self._find_provider_by_host(providers, "cloud.langfuse.com")

        source = self._prompt_provider_selection(providers, "source", default=source_default)
        dest = self._prompt_provider_selection(providers, "destination", default=dest_default)

        if source.id == dest.id:
            raise CommandError("Source and destination must be different TraceProviders.")

        return source.config, dest.config

    def _print_command(self, team_slug: str, source_config: dict, dest_config: dict, options: dict) -> None:
        """Print a runnable command with embedded credentials for local execution."""
        parts = [f"python manage.py migrate_langfuse_data {team_slug}"]
        parts.append(f"  --source-public-key '{source_config['public_key']}'")
        parts.append(f"  --source-secret-key '{source_config['secret_key']}'")
        parts.append(f"  --source-host '{source_config.get('host', DEFAULT_LANGFUSE_HOST)}'")
        parts.append(f"  --dest-public-key '{dest_config['public_key']}'")
        parts.append(f"  --dest-secret-key '{dest_config['secret_key']}'")
        parts.append(f"  --dest-host '{dest_config.get('host', DEFAULT_LANGFUSE_HOST)}'")

        if options["from_timestamp"]:
            parts.append(f"  --from-timestamp '{options['from_timestamp']}'")
        if options["to_timestamp"]:
            parts.append(f"  --to-timestamp '{options['to_timestamp']}'")
        if options["sleep_get"] != 0.7:
            parts.append(f"  --sleep-get {options['sleep_get']}")
        if options["sleep_batch"] != 0.5:
            parts.append(f"  --sleep-batch {options['sleep_batch']}")
        if options["max_retries"] != 4:
            parts.append(f"  --max-retries {options['max_retries']}")
        if options["workers"] != 5:
            parts.append(f"  --workers {options['workers']}")
        if options["dry_run"]:
            parts.append("  --dry-run")

        self.stdout.write("\nRunnable command (paste on local machine):\n")
        self.stdout.write(" \\\n".join(parts))
        self.stdout.write("")

    def _find_provider_by_host(self, providers: list[TraceProvider], host_fragment: str) -> TraceProvider | None:
        """Return the first provider whose host contains host_fragment, or None."""
        for provider in providers:
            if host_fragment in provider.config.get("host", ""):
                return provider
        return None

    def _prompt_provider_selection(
        self, providers: list[TraceProvider], role: str, default: TraceProvider | None = None
    ) -> TraceProvider:
        """Prompt user to select a provider by number, with an optional default."""
        default_idx = providers.index(default) + 1 if default and default in providers else None
        prompt = f"Select {role} provider [1-{len(providers)}]"
        prompt += f" (default: {default_idx}): " if default_idx else ": "
        while True:
            raw = input(prompt).strip()
            if not raw and default_idx:
                return providers[default_idx - 1]
            try:
                idx = int(raw)
                if 1 <= idx <= len(providers):
                    return providers[idx - 1]
            except ValueError:
                pass
            self.stdout.write(f"  Please enter a number between 1 and {len(providers)}.")

    def _init_langfuse_client(self, config: dict, label: str) -> Langfuse:
        """Initialize and verify a Langfuse client."""
        try:
            client = Langfuse(
                public_key=config["public_key"],
                secret_key=config["secret_key"],
                host=config.get("host", DEFAULT_LANGFUSE_HOST),
            )
            self.stdout.write(f"{label} client initialized: {config.get('host', DEFAULT_LANGFUSE_HOST)}")
            client.auth_check()
            self.stdout.write(f"{label} credentials verified.")
            return client
        except Exception as e:
            raise CommandError(f"Error initializing {label.lower()} Langfuse client: {e}") from e

    def _retry_with_backoff(self, fn, description: str, retry_config: RetryConfig, on_exhausted=None):
        """Generic retry with exponential backoff on rate limits.

        Returns the result of fn() on success, or on_exhausted if max retries reached.
        Raises CommandError if on_exhausted is CommandError.
        """
        for attempt in range(1, retry_config.max_retries + 1):
            if attempt > 1:
                time.sleep(retry_config.base_sleep * (2 ** (attempt - 1)))
            try:
                return fn()
            except Exception as e:
                self.stdout.write(f"  Error {description} (attempt {attempt}/{retry_config.max_retries}): {e}")
                if _is_rate_limited(e):
                    sleep_time = 2**attempt
                    self.stdout.write(f"    Rate limit hit. Sleeping {sleep_time}s...")
                    time.sleep(sleep_time)
                elif attempt >= retry_config.max_retries:
                    if isinstance(on_exhausted, CommandError):
                        raise on_exhausted from e
                    return on_exhausted
                else:
                    self.stdout.write("    Transient error, retrying...")
        return on_exhausted

    def _migrate_traces(
        self,
        source_config: dict,
        dest_config: dict,
        from_timestamp_str: str | None = None,
        to_timestamp_str: str | None = None,
        retry_config: RetryConfig | None = None,
        sleep_between_gets: float = 0.7,
        dry_run: bool = False,
        checkpoint_file: str = ".migration_checkpoint.json",
        workers: int = 5,
    ):
        if retry_config is None:
            retry_config = RetryConfig()

        langfuse_source = self._init_langfuse_client(source_config, "Source")
        langfuse_destination = self._init_langfuse_client(dest_config, "Destination")

        from_timestamp = _parse_datetime(from_timestamp_str)
        to_timestamp = _parse_datetime(to_timestamp_str)

        if from_timestamp_str and not from_timestamp:
            raise CommandError(f"Could not parse --from-timestamp: '{from_timestamp_str}'")
        if to_timestamp_str and not to_timestamp:
            raise CommandError(f"Could not parse --to-timestamp: '{to_timestamp_str}'")
        if from_timestamp and to_timestamp and from_timestamp > to_timestamp:
            raise CommandError("--from-timestamp must be earlier than or equal to --to-timestamp")

        migrated_ids, resume_from_timestamp = _load_checkpoint(checkpoint_file)
        if migrated_ids:
            self.stdout.write(f"Resuming: {len(migrated_ids)} traces already migrated (from checkpoint).")

        if resume_from_timestamp:
            resume_dt = _parse_datetime(resume_from_timestamp)
            if resume_dt and (from_timestamp is None or resume_dt > from_timestamp):
                from_timestamp = resume_dt
                self.stdout.write(f"Resuming from checkpoint timestamp: {resume_from_timestamp}")

        checkpoint = CheckpointState(
            migrated_ids=migrated_ids,
            checkpoint_file=checkpoint_file,
            max_resume_timestamp=resume_from_timestamp,
        )

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN — no data will be written to the destination.\n"))

        self.stdout.write(f"\nFetching and migrating traces ({workers} workers)...")
        page = 1
        limit = 100
        total_migrated = 0
        total_failed_fetch = 0
        total_failed_transform = 0
        total_failed_push = 0

        get_retry_config = RetryConfig(max_retries=retry_config.max_retries, base_sleep=sleep_between_gets)

        def _process_single_trace(trace_info):
            """Fetch, transform, and push a single trace. Returns (status, trace_id)."""
            source_trace_id = trace_info.id
            if source_trace_id in checkpoint.migrated_ids:
                self.stdout.write(f"    Skipping {source_trace_id} (already in checkpoint).")
                return "skipped", source_trace_id

            self.stdout.write(f"    Processing source trace ID: {source_trace_id}")

            source_trace_full = self._retry_with_backoff(
                lambda: langfuse_source.api.trace.get(source_trace_id),
                f"fetching details for {source_trace_id}",
                get_retry_config,
            )
            if source_trace_full is None:
                return "failed_fetch", source_trace_id

            try:
                ingestion_batch = _transform_trace_to_ingestion_batch(source_trace_full)
                if not ingestion_batch:
                    self.stdout.write(f"      Skipping {source_trace_id}: transformation returned empty batch.")
                    return "failed_transform", source_trace_id
                batch_event_map = {event.id: event for event in ingestion_batch}
            except Exception as e:
                self.stdout.write(f"      Error transforming trace {source_trace_id}: {e}")
                return "failed_transform", source_trace_id

            push_ok = self._push_batch(
                langfuse_destination,
                ingestion_batch,
                batch_event_map,
                source_trace_id,
                source_trace_full,
                checkpoint,
                retry_config,
                dry_run,
            )
            return ("migrated" if push_ok else "failed_push"), source_trace_id

        while True:
            self.stdout.write(f"\n--- Processing page {page} ---")
            trace_list = self._retry_with_backoff(
                lambda p=page: langfuse_source.api.trace.list(
                    page=p,
                    limit=limit,
                    order_by="timestamp.asc",
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                ),
                f"fetching trace list page {page}",
                retry_config,
                on_exhausted=CommandError(f"Max retries reached fetching page {page}. Stopping."),
            )

            if trace_list is None or not trace_list.data:
                self.stdout.write("No more traces found on this page or in total.")
                break

            meta = trace_list.meta
            self.stdout.write(
                f"  Fetched {len(trace_list.data)} trace summaries on page "
                f"{meta.page}/{getattr(meta, 'total_pages', 'N/A')}."
            )

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_process_single_trace, trace_info): trace_info for trace_info in trace_list.data
                }
                for future in as_completed(futures):
                    status, trace_id = future.result()
                    if status == "migrated":
                        total_migrated += 1
                    elif status == "failed_fetch":
                        total_failed_fetch += 1
                    elif status == "failed_transform":
                        total_failed_transform += 1
                    elif status == "failed_push":
                        total_failed_push += 1

            current_page = getattr(meta, "page", page)
            total_pages = getattr(meta, "total_pages", page)
            if current_page >= total_pages:
                self.stdout.write("Processed the last page according to metadata.")
                break
            page += 1

        self.stdout.write("\n--- Migration Summary ---")
        self.stdout.write(self.style.SUCCESS(f"Successfully migrated: {total_migrated}"))
        if total_failed_fetch:
            self.stdout.write(self.style.ERROR(f"Failed fetching details: {total_failed_fetch}"))
        if total_failed_transform:
            self.stdout.write(self.style.WARNING(f"Failed transforming: {total_failed_transform}"))
        if total_failed_push:
            self.stdout.write(self.style.ERROR(f"Failed pushing/ingesting with errors: {total_failed_push}"))
        self.stdout.write("-------------------------\n")

    def _push_batch(
        self,
        dest_client: Langfuse,
        ingestion_batch: list,
        batch_event_map: dict,
        trace_id: str,
        source_trace_full,
        checkpoint: CheckpointState,
        retry_config: RetryConfig,
        dry_run: bool,
    ) -> bool:
        """Push ingestion batch to destination, retrying on rate limits. Returns True on success."""
        for attempt in range(1, retry_config.max_retries + 1):
            if attempt > 1:
                time.sleep(retry_config.base_sleep * (2 ** (attempt - 1)))
            try:
                if dry_run:
                    self.stdout.write(
                        f"      [DRY RUN] Would ingest {len(ingestion_batch)} events for trace {trace_id}"
                    )
                    trace_ts = _safe_isoformat(getattr(source_trace_full, "timestamp", None))
                    checkpoint.update(trace_id, trace_ts)
                    return True

                response = dest_client.api.ingestion.batch(batch=ingestion_batch)

                if response.errors:
                    self.stdout.write(f"      Ingestion errors for trace {trace_id}:")
                    for i, err in enumerate(response.errors):
                        status = getattr(err, "status", "N/A")
                        message = getattr(err, "message", "No message")
                        failed_id = getattr(err, "id", None)
                        self.stdout.write(f"        Error {i + 1}: Status={status}, Message={message}")
                        if failed_id and failed_id in batch_event_map:
                            failed_event = batch_event_map[failed_id]
                            self.stdout.write(
                                f"          Failed Event Type: {getattr(failed_event, 'type', 'Unknown')}"
                            )
                    return False

                self.stdout.write(self.style.SUCCESS(f"      Successfully ingested trace {trace_id}"))
                trace_ts = _safe_isoformat(getattr(source_trace_full, "timestamp", None))
                checkpoint.update(trace_id, trace_ts)
                return True

            except Exception as e:
                self.stdout.write(
                    f"      Error pushing batch for {trace_id} (attempt {attempt}/{retry_config.max_retries}): {e}"
                )
                if _is_rate_limited(e):
                    sleep_time = 2**attempt
                    self.stdout.write(f"        Rate limit hit. Sleeping {sleep_time}s...")
                    time.sleep(sleep_time)
                elif attempt >= retry_config.max_retries:
                    self.stdout.write(f"        Max retries reached pushing batch for {trace_id}.")
                    return False
                else:
                    self.stdout.write(f"        Non-rate-limit error pushing batch for {trace_id}. Failing this trace.")
                    return False
        return False
