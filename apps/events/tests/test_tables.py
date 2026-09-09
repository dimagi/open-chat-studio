import pytest
from time_machine import travel

from apps.events.models import TimePeriod
from apps.events.tables import SchedulesTable
from apps.utils.factories.events import ScheduledMessageFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


def _params(time_period=TimePeriod.DAYS, frequency=1, repetitions=1):
    return {
        "name": "Test",
        "time_period": time_period,
        "frequency": frequency,
        "repetitions": repetitions,
        "prompt_text": "hi",
    }


@pytest.mark.django_db()
class TestSchedulesTable:
    def _row(self, schedule):
        table = SchedulesTable([schedule.as_dict()])
        return table.rows[0]

    def test_cadence_shows_one_off_for_a_single_run(self):
        session = ExperimentSessionFactory.create()
        schedule = ScheduledMessageFactory.create(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=None,
            custom_schedule_params=_params(repetitions=None),
        )
        row = self._row(schedule)
        assert row.get_cell("cadence") == "One-off"

    def test_cadence_shows_frequency_and_repetitions(self):
        session = ExperimentSessionFactory.create()
        schedule = ScheduledMessageFactory.create(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=None,
            custom_schedule_params=_params(time_period=TimePeriod.DAYS, frequency=2, repetitions=3),
        )
        row = self._row(schedule)
        assert row.get_cell("cadence") == "Every 2 days, 3 times"

    @pytest.mark.parametrize(
        ("cancel", "complete", "expected"),
        [
            pytest.param(True, False, "Cancelled", id="cancelled"),
            pytest.param(False, True, "Completed", id="complete"),
            pytest.param(False, False, "Active", id="active"),
        ],
    )
    def test_status_badge(self, cancel, complete, expected):
        session = ExperimentSessionFactory.create()
        schedule = ScheduledMessageFactory.create(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=None,
            is_complete=complete,
            custom_schedule_params=_params(),
        )
        if cancel:
            schedule.cancel()
        row = self._row(schedule)
        assert expected in row.get_cell("status")

    @travel("2024-01-01", tick=False)
    def test_next_run_shows_a_dash_once_cancelled(self):
        session = ExperimentSessionFactory.create()
        schedule = ScheduledMessageFactory.create(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=None,
            custom_schedule_params=_params(),
        )
        schedule.cancel()
        row = self._row(schedule)
        assert row.get_cell("next_trigger_date") == "-"

    @travel("2024-01-01", tick=False)
    def test_next_run_shows_the_trigger_date_otherwise(self):
        session = ExperimentSessionFactory.create()
        schedule = ScheduledMessageFactory.create(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=None,
            custom_schedule_params=_params(),
        )
        row = self._row(schedule)
        assert "2024" in row.get_cell("next_trigger_date")

    def test_experiment_column_is_excluded_for_a_single_session_view(self):
        session = ExperimentSessionFactory.create()
        schedule_dict = ScheduledMessageFactory.create(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=None,
            custom_schedule_params=_params(),
        ).as_dict()
        table = SchedulesTable([schedule_dict])
        table.exclude = ("experiment",)
        assert "experiment" not in table.columns.names()

    def test_experiment_column_is_shown_when_aggregating_across_chatbots(self):
        session = ExperimentSessionFactory.create()
        schedule_dict = ScheduledMessageFactory.create(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=None,
            custom_schedule_params=_params(),
        ).as_dict()
        schedule_dict["experiment"] = session.experiment
        table = SchedulesTable([schedule_dict])
        assert "experiment" in table.columns.names()
        assert table.rows[0].get_cell("experiment") == session.experiment
