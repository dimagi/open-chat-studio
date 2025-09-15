from unittest.mock import patch

import pytest
import time_machine
from django.utils import timezone

from apps.experiments.utils import get_experiment_trend_data
from apps.trace.models import Trace, TraceStatus


@pytest.mark.django_db()
class TestExperimentUtils:
    def test_get_experiment_trend_data_with_no_errors(self, experiment):
        """Test that the function returns an array of zeros when there are no error traces"""
        success, errors = get_experiment_trend_data(experiment)
        empty_data = [0] * 49
        assert errors == empty_data
        assert success == empty_data

    @patch("apps.experiments.utils.timezone.now")
    def test_get_experiment_trend_data_with_errors(self, mock_now, experiment):
        """Test that the function returns error counts when there are error traces"""
        # Mock current time
        mock_time = timezone.datetime(2024, 1, 15, 12, 0, 0)
        mock_now.return_value = mock_time

        # Create error traces at different times
        error_time_1 = mock_time - timezone.timedelta(hours=2)
        error_time_2 = mock_time - timezone.timedelta(hours=2)  # Same hour as above
        error_time_3 = mock_time - timezone.timedelta(hours=5)

        # Create traces with error status
        Trace.objects.create(
            experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=error_time_1, duration=1
        )
        Trace.objects.create(
            experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=error_time_2, duration=1
        )
        Trace.objects.create(
            experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=error_time_3, duration=1
        )

        # Create a non-error trace to ensure it's not counted
        Trace.objects.create(
            experiment=experiment, team=experiment.team, status=TraceStatus.SUCCESS, timestamp=error_time_1, duration=1
        )

        success, errors = get_experiment_trend_data(experiment)

        # Should return actual error counts (2 errors in one hour, 1 in another)
        assert isinstance(errors, list)
        assert any(count > 0 for count in errors)
        # We expect to find 2 errors in the 2-hour bucket and 1 in the 5-hour bucket
        assert sum(errors) == 3
        assert sum(success) == 1

    def test_get_experiment_trend_data_only_recent_errors(self, experiment):
        """Test that only errors within the last 2 days are counted"""
        # Mock current time
        with time_machine.travel("2025-08-15 12:00:00"):
            # Create an error trace outside the 2-day window
            Trace.objects.create(experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, duration=1)

        # Create an error trace within the 2-day window
        with time_machine.travel("2025-08-21 12:00:00"):
            # Create an error trace outside the 2-day window
            Trace.objects.create(experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, duration=1)

            Trace.objects.create(experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, duration=1)

            success, error = get_experiment_trend_data(experiment)

            # Should only count the recent error
            assert sum(error) == 2
            assert experiment.traces.filter(status=TraceStatus.ERROR).count() == 3
            assert sum(success) == 0
