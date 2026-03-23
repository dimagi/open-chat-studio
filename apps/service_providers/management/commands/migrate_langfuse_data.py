#!/usr/bin/env python
"""Django management command to migrate Langfuse traces between projects."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from langfuse import Langfuse
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

        try:
            team = Team.objects.get(slug=team_slug)
        except Team.DoesNotExist as e:
            raise CommandError(f"Team with slug '{team_slug}' not found.") from e

        providers = list(TraceProvider.objects.filter(team=team, type=TraceProviderType.langfuse).order_by("name"))
        if not providers:
            raise CommandError(f"No Langfuse TraceProviders found for team '{team_slug}'.")

        self.stdout.write(f"\nLangfuse TraceProviders for team '{team}':\n")
        for i, provider in enumerate(providers, 1):
            host = provider.config.get("host", "https://cloud.langfuse.com")
            self.stdout.write(f"  [{i}] {provider.name}  ({host})")
        self.stdout.write("")

        source_default = self._find_provider_by_host(providers, "langfuse.openchatstudio.com")
        dest_default = self._find_provider_by_host(providers, "cloud.langfuse.com")

        source = self._prompt_provider_selection(providers, "source", default=source_default)
        dest = self._prompt_provider_selection(providers, "destination", default=dest_default)

        if source.id == dest.id:
            raise CommandError("Source and destination must be different TraceProviders.")

        checkpoint_file = options["checkpoint_file"] or f".migration_checkpoint_{team_slug}.json"
        if options["reset_checkpoint"] and os.path.exists(checkpoint_file):
            os.remove(checkpoint_file)
            self.stdout.write(f"Checkpoint file '{checkpoint_file}' deleted.\n")

        self.stdout.write(self.style.WARNING("\nWARNING: Migrates full trace data. PRESERVES ORIGINAL TRACE IDs."))
        self.stdout.write(self.style.WARNING("Ensure no ID collisions in the destination project!\n"))
        self.stdout.write(f"Source:      {source.name}  ({source.config.get('host', 'https://cloud.langfuse.com')})")
        self.stdout.write(f"Destination: {dest.name}  ({dest.config.get('host', 'https://cloud.langfuse.com')})")
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
            source_config=source.config,
            dest_config=dest.config,
            from_timestamp_str=options["from_timestamp"],
            to_timestamp_str=options["to_timestamp"],
            sleep_between_gets=options["sleep_get"],
            sleep_between_batches=options["sleep_batch"],
            max_retries=options["max_retries"],
            dry_run=options["dry_run"],
            checkpoint_file=checkpoint_file,
            workers=options["workers"],
        )

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

    def _migrate_traces(
        self,
        source_config,
        dest_config,
        from_timestamp_str=None,
        to_timestamp_str=None,
        sleep_between_gets=0.7,
        sleep_between_batches=0.5,
        max_retries=4,
        dry_run=False,
        checkpoint_file=".migration_checkpoint.json",
        workers=5,
    ):
        try:
            langfuse_source = Langfuse(
                public_key=source_config["public_key"],
                secret_key=source_config["secret_key"],
                host=source_config.get("host", "https://cloud.langfuse.com"),
            )
            self.stdout.write(f"Source client initialized: {source_config.get('host', 'https://cloud.langfuse.com')}")
            langfuse_source.auth_check()
            self.stdout.write("Source credentials verified.")
        except Exception as e:
            raise CommandError(f"Error initializing source Langfuse client: {e}") from e

        try:
            langfuse_destination = Langfuse(
                public_key=dest_config["public_key"],
                secret_key=dest_config["secret_key"],
                host=dest_config.get("host", "https://cloud.langfuse.com"),
            )
            self.stdout.write(
                f"Destination client initialized: {dest_config.get('host', 'https://cloud.langfuse.com')}"
            )
            langfuse_destination.auth_check()
            self.stdout.write("Destination credentials verified.")
        except Exception as e:
            raise CommandError(f"Error initializing destination Langfuse client: {e}") from e

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

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN — no data will be written to the destination.\n"))

        self.stdout.write(f"\nFetching and migrating traces ({workers} workers)...")
        page = 1
        limit = 100
        total_migrated = 0
        total_failed_fetch = 0
        total_failed_transform = 0
        total_failed_push = 0
        checkpoint_lock = threading.Lock()

        def _process_single_trace(trace_info):
            """Fetch, transform, and push a single trace. Returns (status, trace_id)."""
            source_trace_id = trace_info.id
            if source_trace_id in migrated_ids:
                self.stdout.write(f"    Skipping {source_trace_id} (already in checkpoint).")
                return "skipped", source_trace_id

            self.stdout.write(f"    Processing source trace ID: {source_trace_id}")

            source_trace_full = self._fetch_trace_detail_with_retry(
                langfuse_source, source_trace_id, sleep_between_gets, max_retries
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

            push_ok = self._push_batch_with_retry(
                langfuse_destination,
                ingestion_batch,
                batch_event_map,
                source_trace_id,
                source_trace_full,
                migrated_ids,
                checkpoint_file,
                resume_from_timestamp,
                sleep_between_batches,
                max_retries,
                dry_run,
                checkpoint_lock,
            )
            return ("migrated" if push_ok else "failed_push"), source_trace_id

        while True:
            self.stdout.write(f"\n--- Processing page {page} ---")
            trace_list = self._fetch_trace_list_with_retry(
                langfuse_source, page, limit, from_timestamp, to_timestamp, max_retries
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

    def _fetch_trace_list_with_retry(self, client, page, limit, from_timestamp, to_timestamp, max_retries):
        """Fetch a page of trace summaries, retrying on rate limits."""
        for attempt in range(1, max_retries + 1):
            try:
                return client.api.trace.list(
                    page=page,
                    limit=limit,
                    order_by="timestamp.asc",
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                )
            except Exception as e:
                self.stdout.write(f"  Error fetching trace list page {page} (attempt {attempt}/{max_retries}): {e}")
                if "429" in str(e):
                    sleep_time = 2**attempt
                    self.stdout.write(f"    Rate limit hit. Sleeping {sleep_time}s...")
                    time.sleep(sleep_time)
                elif attempt >= max_retries:
                    raise CommandError(f"Max retries reached fetching page {page}. Stopping.") from e
                else:
                    time.sleep(2)
        return None

    def _fetch_trace_detail_with_retry(self, client, trace_id, sleep_between_gets, max_retries):
        """Fetch full trace details, retrying on rate limits."""
        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(sleep_between_gets * (2 ** (attempt - 1)))
            try:
                return client.api.trace.get(trace_id)
            except Exception as e:
                self.stdout.write(f"      Error fetching details for {trace_id} (attempt {attempt}/{max_retries}): {e}")
                if "429" in str(e):
                    time.sleep(2**attempt)
                elif attempt >= max_retries:
                    self.stdout.write(f"        Max retries reached fetching details for {trace_id}.")
                    return None
                else:
                    self.stdout.write(f"        Transient error for {trace_id}, retrying...")
        return None

    def _push_batch_with_retry(
        self,
        dest_client,
        ingestion_batch,
        batch_event_map,
        trace_id,
        source_trace_full,
        migrated_ids,
        checkpoint_file,
        resume_from_timestamp,
        sleep_between_batches,
        max_retries,
        dry_run,
        checkpoint_lock=None,
    ) -> bool:
        """Push ingestion batch to destination, retrying on rate limits. Returns True on success."""
        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(sleep_between_batches * (2 ** (attempt - 1)))
            try:
                if dry_run:
                    self.stdout.write(
                        f"      [DRY RUN] Would ingest {len(ingestion_batch)} events for trace {trace_id}"
                    )
                    trace_ts = _safe_isoformat(getattr(source_trace_full, "timestamp", None))
                    self._update_checkpoint(migrated_ids, trace_id, checkpoint_file, trace_ts, checkpoint_lock)
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
                self._update_checkpoint(migrated_ids, trace_id, checkpoint_file, trace_ts, checkpoint_lock)
                return True

            except Exception as e:
                self.stdout.write(f"      Error pushing batch for {trace_id} (attempt {attempt}/{max_retries}): {e}")
                if "429" in str(e):
                    sleep_time = 2**attempt
                    self.stdout.write(f"        Rate limit hit. Sleeping {sleep_time}s...")
                    time.sleep(sleep_time)
                elif attempt >= max_retries:
                    self.stdout.write(f"        Max retries reached pushing batch for {trace_id}.")
                    return False
                else:
                    self.stdout.write(f"        Non-rate-limit error pushing batch for {trace_id}. Failing this trace.")
                    return False
        return False

    def _update_checkpoint(self, migrated_ids, trace_id, checkpoint_file, trace_ts, checkpoint_lock=None):
        """Thread-safe checkpoint update."""
        if checkpoint_lock:
            with checkpoint_lock:
                migrated_ids.add(trace_id)
                _save_checkpoint(checkpoint_file, migrated_ids, resume_from_timestamp=trace_ts)
        else:
            migrated_ids.add(trace_id)
            _save_checkpoint(checkpoint_file, migrated_ids, resume_from_timestamp=trace_ts)
