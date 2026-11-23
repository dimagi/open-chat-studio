from unittest.mock import patch

import pytest

from apps.experiments.task_utils import CUSTOM_ERROR_MESSAGE, DEFAULT_ERROR_MESSAGE, get_message_task_response


def _make_progress(complete, success, result=None):
    return {"complete": complete, "success": success, "result": result or {}}


def test_returns_empty_dict_when_complete_success_no_result():
    progress = _make_progress(complete=True, success=True, result=None)
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = {**progress, "result": None}
        result = get_message_task_response("task-1")
    assert result == {}


def test_incomplete_task_returns_complete_false():
    progress = {"complete": False, "success": False, "result": {}}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response("task-1")
    assert result["complete"] is False
    assert result["error_msg"] is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param("internal failure", DEFAULT_ERROR_MESSAGE, id="generic-error"),
        pytest.param("Invalid parameter: model", CUSTOM_ERROR_MESSAGE, id="invalid-parameter"),
    ],
)
def test_an_error_result_never_returns_the_raw_error(error, expected):
    progress = {"complete": True, "success": True, "result": {"error": error}}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response("task-1")
    assert result["error_msg"] == expected


def test_failed_task_returns_default_error():
    progress = {"complete": True, "success": False, "result": {}}
    with patch("apps.experiments.task_utils.Progress") as mock_progress_cls:
        mock_progress_cls.return_value.get_info.return_value = progress
        result = get_message_task_response("task-1")
    assert result["error_msg"] == DEFAULT_ERROR_MESSAGE
