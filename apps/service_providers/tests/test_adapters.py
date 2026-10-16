from types import SimpleNamespace

import pytest

from apps.service_providers.llm_service.adapters import BaseAdapter

BUILT_IN_TOOL = {"type": "web_search"}
REMINDER_TOOL = SimpleNamespace(name="one-off-reminder")
OTHER_TOOL = SimpleNamespace(name="update-user-data")


class _Adapter(BaseAdapter):
    def __init__(self, tools, disabled_tools):
        self.tools = tools
        self.disabled_tools = disabled_tools


@pytest.mark.parametrize(
    ("disabled_tools", "expected"),
    [
        pytest.param([], [BUILT_IN_TOOL, REMINDER_TOOL, OTHER_TOOL], id="nothing-disabled"),
        pytest.param(["one-off-reminder"], [BUILT_IN_TOOL, OTHER_TOOL], id="named-tool-removed-built-in-kept"),
        pytest.param(["unknown"], [BUILT_IN_TOOL, REMINDER_TOOL, OTHER_TOOL], id="unknown-name-removes-nothing"),
    ],
)
def test_get_allowed_tools(disabled_tools, expected):
    adapter = _Adapter([BUILT_IN_TOOL, REMINDER_TOOL, OTHER_TOOL], disabled_tools)

    assert adapter.get_allowed_tools() == expected
