"""Tests for the migrate_langfuse_data management command helpers."""

from __future__ import annotations

import datetime as dt
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management.base import CommandError
from langfuse.api.core.api_error import ApiError

from apps.service_providers.management.commands.migrate_langfuse_data import (
    CheckpointState,
    RetryConfig,
    _is_rate_limited,
    _load_checkpoint,
    _parse_datetime,
    _safe_isoformat,
    _save_checkpoint,
    _transform_trace_to_ingestion_batch,
)


class TestSafeIsoformat:
    def test_none_returns_none(self):
        assert _safe_isoformat(None) is None

    def test_aware_datetime(self):
        dt_obj = dt.datetime(2024, 1, 15, 10, 30, 0, tzinfo=dt.UTC)
        result = _safe_isoformat(dt_obj)
        assert result == "2024-01-15T10:30:00.000Z"

    def test_naive_datetime_gets_utc(self):
        dt_obj = dt.datetime(2024, 1, 15, 10, 30, 0)
        result = _safe_isoformat(dt_obj)
        assert result == "2024-01-15T10:30:00.000Z"

    def test_string_passthrough_valid_iso(self):
        iso_str = "2024-01-15T10:30:00Z"
        assert _safe_isoformat(iso_str) == iso_str

    def test_string_invalid_returns_none(self):
        assert _safe_isoformat("not-a-date") is None

    def test_non_datetime_non_string_returns_none(self):
        assert _safe_isoformat(12345) is None

    def test_offset_datetime(self):
        tz = dt.timezone(dt.timedelta(hours=5))
        dt_obj = dt.datetime(2024, 1, 15, 10, 30, 0, tzinfo=tz)
        result = _safe_isoformat(dt_obj)
        assert result is not None
        assert "+" in result


class TestParseDatetime:
    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_datetime("") is None

    def test_z_suffix(self):
        result = _parse_datetime("2024-01-15T10:30:00Z")
        assert result == dt.datetime(2024, 1, 15, 10, 30, 0, tzinfo=dt.UTC)

    def test_offset_string(self):
        result = _parse_datetime("2024-01-15T10:30:00+00:00")
        assert result == dt.datetime(2024, 1, 15, 10, 30, 0, tzinfo=dt.UTC)

    def test_naive_gets_utc(self):
        result = _parse_datetime("2024-01-15T10:30:00")
        assert result is not None
        assert result.tzinfo == dt.UTC

    def test_invalid_returns_none(self):
        assert _parse_datetime("not-a-date") is None

    def test_non_string_returns_none(self):
        assert _parse_datetime(12345) is None


class TestCheckpointRoundTrip:
    def test_save_and_load(self, tmp_path):
        filepath = str(tmp_path / "checkpoint.json")
        ids = {"trace-1", "trace-2"}
        _save_checkpoint(filepath, ids, resume_from_timestamp="2024-01-15T10:00:00Z")

        loaded_ids, resume_ts = _load_checkpoint(filepath)
        assert loaded_ids == ids
        assert resume_ts == "2024-01-15T10:00:00Z"

    def test_load_nonexistent(self, tmp_path):
        filepath = str(tmp_path / "nonexistent.json")
        ids, ts = _load_checkpoint(filepath)
        assert ids == set()
        assert ts is None

    def test_load_invalid_json(self, tmp_path):
        filepath = str(tmp_path / "bad.json")
        with open(filepath, "w") as f:
            f.write("not json")
        with pytest.raises(CommandError, match="Invalid checkpoint file"):
            _load_checkpoint(filepath)

    def test_atomic_write(self, tmp_path):
        filepath = str(tmp_path / "checkpoint.json")
        _save_checkpoint(filepath, {"id-1"}, resume_from_timestamp="ts1")
        # tmp file should not remain
        assert not (tmp_path / "checkpoint.json.tmp").exists()
        assert (tmp_path / "checkpoint.json").exists()


class TestCheckpointState:
    def test_update_records_id_only(self, tmp_path):
        filepath = str(tmp_path / "checkpoint.json")
        state = CheckpointState(migrated_ids=set(), checkpoint_file=filepath)

        state.update("trace-1")
        state.update("trace-2")

        assert state.migrated_ids == {"trace-1", "trace-2"}
        # update() should not change resume timestamp
        assert state.max_resume_timestamp is None

    def test_advance_resume_timestamp_monotonic(self, tmp_path):
        filepath = str(tmp_path / "checkpoint.json")
        state = CheckpointState(migrated_ids=set(), checkpoint_file=filepath)

        state.advance_resume_timestamp("2024-01-15T10:00:00Z")
        assert state.max_resume_timestamp == "2024-01-15T10:00:00Z"

        # Earlier timestamp should not regress
        state.advance_resume_timestamp("2024-01-15T09:00:00Z")
        assert state.max_resume_timestamp == "2024-01-15T10:00:00Z"

        # Later timestamp should advance
        state.advance_resume_timestamp("2024-01-15T11:00:00Z")
        assert state.max_resume_timestamp == "2024-01-15T11:00:00Z"

    def test_advance_none_doesnt_overwrite(self, tmp_path):
        filepath = str(tmp_path / "checkpoint.json")
        state = CheckpointState(
            migrated_ids=set(),
            checkpoint_file=filepath,
            max_resume_timestamp="2024-01-15T10:00:00Z",
        )
        state.advance_resume_timestamp(None)
        assert state.max_resume_timestamp == "2024-01-15T10:00:00Z"

    def test_thread_safety(self, tmp_path):
        filepath = str(tmp_path / "checkpoint.json")
        state = CheckpointState(migrated_ids=set(), checkpoint_file=filepath)

        def update_batch(start):
            for i in range(100):
                state.update(f"trace-{start}-{i}")

        threads = [threading.Thread(target=update_batch, args=(n * 100,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(state.migrated_ids) == 500


class TestIsRateLimited:
    def test_api_error_429(self):
        exc = ApiError(status_code=429, body="rate limited")
        assert _is_rate_limited(exc) is True

    def test_api_error_500(self):
        exc = ApiError(status_code=500, body="server error")
        assert _is_rate_limited(exc) is False

    def test_generic_exception(self):
        assert _is_rate_limited(ValueError("429 in message")) is False

    def test_api_error_none_status(self):
        exc = ApiError(status_code=None, body="unknown")
        assert _is_rate_limited(exc) is False


class TestTransformTraceToIngestionBatch:
    def _make_trace(self, observations=None, scores=None):
        return SimpleNamespace(
            id="trace-123",
            timestamp=dt.datetime(2024, 1, 15, 10, 0, 0, tzinfo=dt.UTC),
            name="test-trace",
            user_id="user-1",
            input={"msg": "hello"},
            output={"msg": "world"},
            session_id="session-1",
            release="v1",
            version="1",
            metadata={"key": "value"},
            tags=["tag1"],
            public=False,
            environment="production",
            observations=observations or [],
            scores=scores or [],
        )

    def _make_observation(self, obs_type="SPAN", obs_id="obs-1", parent_id=None):
        return SimpleNamespace(
            id=obs_id,
            type=obs_type,
            name=f"test-{obs_type.lower()}",
            start_time=dt.datetime(2024, 1, 15, 10, 0, 1, tzinfo=dt.UTC),
            end_time=dt.datetime(2024, 1, 15, 10, 0, 2, tzinfo=dt.UTC),
            metadata={"obs_key": "obs_val"},
            model_parameters=None,
            input={"in": "data"},
            output={"out": "data"},
            level="DEFAULT",
            status_message=None,
            parent_observation_id=parent_id,
            version="1",
            environment="production",
            completion_start_time=None,
            model=None,
            usage=None,
            cost_details=None,
            usage_details=None,
        )

    def test_empty_trace(self):
        trace = self._make_trace()
        events = _transform_trace_to_ingestion_batch(trace)
        # Should have just the trace create event
        assert len(events) == 1
        assert events[0].body.id == "trace-123"

    def test_span_observation(self):
        obs = self._make_observation("SPAN")
        trace = self._make_trace(observations=[obs])
        events = _transform_trace_to_ingestion_batch(trace)
        assert len(events) == 2  # trace + span

    def test_event_observation(self):
        obs = self._make_observation("EVENT")
        trace = self._make_trace(observations=[obs])
        events = _transform_trace_to_ingestion_batch(trace)
        assert len(events) == 2

    def test_generation_observation(self):
        obs = self._make_observation("GENERATION")
        trace = self._make_trace(observations=[obs])
        events = _transform_trace_to_ingestion_batch(trace)
        assert len(events) == 2

    def test_unknown_observation_type_skipped(self):
        obs = self._make_observation("UNKNOWN_TYPE")
        trace = self._make_trace(observations=[obs])
        events = _transform_trace_to_ingestion_batch(trace)
        assert len(events) == 1  # only trace, unknown skipped

    def test_parent_observation_remapping(self):
        parent = self._make_observation("SPAN", obs_id="parent-obs")
        child = self._make_observation("SPAN", obs_id="child-obs", parent_id="parent-obs")
        trace = self._make_trace(observations=[parent, child])
        events = _transform_trace_to_ingestion_batch(trace)
        assert len(events) == 3

        # Child should reference the new parent ID, not the original
        child_event = events[2]
        parent_event = events[1]
        assert child_event.body.parent_observation_id == parent_event.body.id
        assert child_event.body.parent_observation_id != "parent-obs"

    def test_score_transformation(self):
        score = SimpleNamespace(
            id="score-1",
            name="quality",
            value=0.95,
            data_type="NUMERIC",
            source="API",
            comment=None,
            observation_id=None,
            timestamp=dt.datetime(2024, 1, 15, 10, 0, 0, tzinfo=dt.UTC),
            config_id=None,
            metadata={},
            environment="production",
        )
        trace = self._make_trace(scores=[score])
        events = _transform_trace_to_ingestion_batch(trace)
        assert len(events) == 2  # trace + score

    def test_score_with_none_value_skipped(self):
        score = SimpleNamespace(
            id="score-1",
            name="quality",
            value=None,
            data_type="NUMERIC",
            source="API",
            comment=None,
            observation_id=None,
            timestamp=dt.datetime(2024, 1, 15, 10, 0, 0, tzinfo=dt.UTC),
            config_id=None,
            metadata={},
            environment="production",
        )
        trace = self._make_trace(scores=[score])
        events = _transform_trace_to_ingestion_batch(trace)
        assert len(events) == 1  # only trace, score skipped

    def test_preserves_trace_id(self):
        trace = self._make_trace()
        events = _transform_trace_to_ingestion_batch(trace)
        assert events[0].body.id == "trace-123"

    @patch("apps.service_providers.management.commands.migrate_langfuse_data.uuid.uuid4")
    def test_new_observation_ids(self, mock_uuid):
        mock_uuid.side_effect = [
            "event-uuid",  # trace event id
            "new-obs-uuid",  # new observation id
            "obs-event-uuid",  # observation event id
        ]
        obs = self._make_observation("SPAN", obs_id="original-obs-id")
        trace = self._make_trace(observations=[obs])
        events = _transform_trace_to_ingestion_batch(trace)
        # Observation should get a new ID, not keep the original
        assert events[1].body.id == "new-obs-uuid"


class TestRetryConfig:
    def test_defaults(self):
        config = RetryConfig()
        assert config.max_retries == 4
        assert config.base_sleep == 0.5

    def test_custom(self):
        config = RetryConfig(max_retries=10, base_sleep=1.0)
        assert config.max_retries == 10
        assert config.base_sleep == 1.0
