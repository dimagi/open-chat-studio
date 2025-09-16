import pytest
import time_machine

from apps.trace.models import Trace, TraceStatus


# TODO: Move to experiment test file
@pytest.mark.django_db()
class TestExperimentUtils:
    def test_get_experiment_trend_data_with_no_errors(self, experiment):
        """Test that the function returns an array of zeros when there are no error traces"""
        success, errors = experiment.get_trend_data()
        empty_data = [0] * 49
        assert errors == empty_data
        assert success == empty_data

    def test_get_experiment_trend_data_with_errors(self, experiment):
        """Test that the function returns error counts when there are error traces"""
        # Create traces with error status
        with time_machine.travel("2025-01-01 12:00:00") as curr_time:
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.SUCCESS, timestamp=curr_time, duration=1
            )
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
            )

        with time_machine.travel("2025-01-01 10:00:00"):
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
            )

        with time_machine.travel("2025-01-01 7:00:00"):
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
            )

        with time_machine.travel("2025-01-01 13:00:00") as curr_time:
            success, errors = experiment.get_trend_data()

        # Should return actual error counts (2 errors in one hour, 1 in another)
        assert isinstance(errors, list)
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

            success, error = experiment.get_trend_data()

            # Should only count the recent error
            assert sum(error) == 2
            assert experiment.traces.filter(status=TraceStatus.ERROR).count() == 3
            assert sum(success) == 0
