"""Unit tests for the usage_metrics filter vocabulary and metric functions."""

import dataclasses

import pytest

from apps.usage_metrics.filters import UsageFilters


class TestUsageFilters:
    def test_defaults_mean_unfiltered(self):
        filters = UsageFilters()
        assert filters.experiment_ids is None
        assert filters.participant_ids is None
        assert filters.platform is None
        assert filters.tag_ids is None
        assert filters.include_archived is True

    def test_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            UsageFilters().platform = "web"  # type: ignore
