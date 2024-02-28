from apps.analysis.core import NoParams, PipelineContext, StepContext
from apps.analysis.tasks import RunLogHandler
from apps.analysis.tests.demo_steps import StrInt


class FakeHandler(RunLogHandler):
    def __init__(self, logs, step_id):
        super().__init__(None, step_id)
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
    pipeline_context = PipelineContext(log_handler_factory=lambda step_id: FakeHandler(logs, step_id))
    step.invoke(StepContext.initial("1"), pipeline_context)


def check_logs(logs):
    logs = [(entry["logger"], entry["message"]) for entry in logs]
    assert logs == [
        ("StrInt", "Running step StrInt"),
        ("StrInt", "Params: "),
        ("StrInt", "Step StrInt complete"),
    ]
