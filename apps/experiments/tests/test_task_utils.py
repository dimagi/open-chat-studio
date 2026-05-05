from unittest.mock import MagicMock, patch

import pytest

from apps.experiments.task_utils import DEFAULT_ERROR_MESSAGE, get_message_task_response


def _make_progress(complete, success, result=None):
    return {"complete": complete, "success": success, "result": result or {}}


@pytest.fixture()
def experiment():
    exp = MagicMock()
    exp.debug_mode_enabled = False
    return exp


def test_returns_empty_dict_when_complete_success_no_result(experiment):
    progress = _make_progress(complete=True, success=True, result=None)
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = {**progress, "result": None}
        result = get_message_task_response(experiment, "task-1")
    assert result == {}


def test_incomplete_task_returns_complete_false(experiment):
    progress = {"complete": False, "success": False, "result": {}}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response(experiment, "task-1")
    assert result["complete"] is False
    assert result["error_msg"] is False


def test_user_facing_error_passed_through(experiment):
    result_data = {"error": "too big", "user_facing_error": True}
    progress = {"complete": True, "success": True, "result": result_data}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response(experiment, "task-1")
    assert result["error_msg"] == "too big"
    assert result["user_facing_error"] is True


def test_generic_error_returns_default_message_when_debug_disabled(experiment):
    experiment.debug_mode_enabled = False
    result_data = {"error": "internal failure"}
    progress = {"complete": True, "success": True, "result": result_data}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response(experiment, "task-1")
    assert result["error_msg"] == DEFAULT_ERROR_MESSAGE
    assert result["user_facing_error"] is False


def test_generic_error_returns_raw_message_when_debug_enabled(experiment):
    experiment.debug_mode_enabled = True
    result_data = {"error": "internal failure"}
    progress = {"complete": True, "success": True, "result": result_data}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response(experiment, "task-1")
    assert result["error_msg"] == "internal failure"


def test_failed_task_returns_default_error(experiment):
    progress = {"complete": True, "success": False, "result": {}}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response(experiment, "task-1")
    assert result["error_msg"] == DEFAULT_ERROR_MESSAGE
