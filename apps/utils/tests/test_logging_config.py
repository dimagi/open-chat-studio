import pytest
from django.conf import settings

# `ocs` propagates to the root logger only under test, so that caplog sees its records.
TEST_ONLY_PROPAGATION = {"ocs"}


def _ancestors(name):
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)] + [""]


@pytest.mark.parametrize(
    "name",
    [name for name, config in settings.LOGGING["loggers"].items() if config.get("handlers") and name != ""],
)
def test_configured_logger_does_not_emit_twice(name):
    loggers = settings.LOGGING["loggers"]
    if name in TEST_ONLY_PROPAGATION or loggers[name].get("propagate", True) is False:
        return
    for ancestor in _ancestors(name):
        config = loggers.get(ancestor)
        if config is None:
            continue
        assert not config.get("handlers"), f"{name} and {ancestor or 'root'} both write the same record"
        if config.get("propagate", True) is False or ancestor in TEST_ONLY_PROPAGATION:
            break
