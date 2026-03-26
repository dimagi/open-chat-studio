from unittest.mock import MagicMock

from apps.channels.channels_v2.stages.terminal import ActivityTrackingStage
from apps.channels.tests.channels.conftest import make_context


class TestActivityTrackingStage:
    def setup_method(self):
        self.stage = ActivityTrackingStage()

    def test_should_not_run_without_session(self):
        ctx = make_context(experiment_session=None)
        assert self.stage.should_run(ctx) is False

    def test_updates_last_activity(self):
        session = MagicMock()
        session.experiment_versions = None
        experiment = MagicMock()
        experiment.is_a_version = False
        ctx = make_context(experiment=experiment, experiment_session=session)

        self.stage(ctx)

        assert session.last_activity_at is not None
        session.save.assert_called_once()
        save_kwargs = session.save.call_args
        assert "last_activity_at" in save_kwargs.kwargs.get("update_fields", save_kwargs[1].get("update_fields", []))

    def test_version_tracking_adds_new(self):
        session = MagicMock()
        session.experiment_versions = [1, 2]
        experiment = MagicMock()
        experiment.is_a_version = True
        experiment.version_number = 3
        ctx = make_context(experiment=experiment, experiment_session=session)

        self.stage(ctx)

        assert 3 in session.experiment_versions
        session.save.assert_called_once()
        save_kwargs = session.save.call_args
        update_fields = save_kwargs.kwargs.get("update_fields", save_kwargs[1].get("update_fields", []))
        assert "experiment_versions" in update_fields

    def test_version_tracking_no_duplicate(self):
        session = MagicMock()
        session.experiment_versions = [1, 2, 3]
        experiment = MagicMock()
        experiment.is_a_version = True
        experiment.version_number = 3
        ctx = make_context(experiment=experiment, experiment_session=session)

        self.stage(ctx)

        # Version 3 should not be duplicated
        assert session.experiment_versions.count(3) == 1
        save_kwargs = session.save.call_args
        update_fields = save_kwargs.kwargs.get("update_fields", save_kwargs[1].get("update_fields", []))
        assert "experiment_versions" not in update_fields

    def test_non_version_does_not_touch_versions(self):
        session = MagicMock()
        session.experiment_versions = [1, 2]
        experiment = MagicMock()
        experiment.is_a_version = False
        ctx = make_context(experiment=experiment, experiment_session=session)

        self.stage(ctx)

        # experiment_versions should not change
        assert session.experiment_versions == [1, 2]
        save_kwargs = session.save.call_args
        update_fields = save_kwargs.kwargs.get("update_fields", save_kwargs[1].get("update_fields", []))
        assert "experiment_versions" not in update_fields
