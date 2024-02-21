from apps.analysis.core import NoParams, PipelineContext, StepContext
from apps.analysis.tasks import RunLogHandler
from apps.analysis.tests.demo_steps import StrInt


class FakeHandler(RunLogHandler):
    def __init__(self, logs):
        super().__init__(None)
        self.logs = logs

    def flush_db(self):
        pass


def test_logging_no_duplicates():
    """Make sure that independent runs don't duplicate logs."""
    logs = []  # shared log storage for all runs to simulate DB
    _run_step(logs)
    check_logs(logs)

    logs.clear()
    _run_step(logs)
    check_logs(logs)


def _run_step(logs):
    step = StrInt(params=NoParams())
    step.invoke(StepContext.initial("1"), PipelineContext(log_handler=FakeHandler(logs)))


def check_logs(logs):
    logs = [(entry["logger"], entry["message"]) for entry in logs]
    assert logs == [
        ("StrInt", "Running step StrInt"),
        ("StrInt", "Params: "),
        ("StrInt", "Step StrInt complete"),
    ]
