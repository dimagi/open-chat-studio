"""Guard tests for Celery queue routing.

Tasks are split across queues (see ``apps.utils.celery.Queues``) so that long-running work —
evaluation fan-out, indexing, exports — can never starve inbound chat messages of workers. That
only holds if *every* task says which queue it belongs on: an un-annotated task silently falls
back to the default queue, which is the latency-sensitive chat queue, i.e. exactly the
regression the split exists to prevent.

Two complementary checks:

* **Source scan** — parses every module under ``apps/`` and requires an explicit ``queue=`` on
  each ``@shared_task``. This is the exhaustive one: it sees tasks in modules that nothing
  happens to import, which the runtime registry would miss.
* **Registry scan** — for tasks that are registered, checks the declared queue actually exists
  in ``settings.CELERY_TASK_QUEUES`` and that Celery's router agrees. Catches a typo'd queue
  name or a broken queue declaration, which the source scan alone would wave through.
"""

import ast
from functools import cache
from pathlib import Path

import pytest
from celery import current_app
from django.conf import settings

from apps.utils.celery import Queues

APPS_DIR = Path(__file__).resolve().parents[2]

TASK_DECORATORS = ("shared_task", "task")


def _is_task_decorator(node: ast.expr) -> bool:
    """True for ``@shared_task``, ``@shared_task(...)``, ``@app.task(...)`` and friends."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr in TASK_DECORATORS
    return isinstance(node, ast.Name) and node.id in TASK_DECORATORS


def _declared_queue(decorator: ast.expr) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None  # bare @shared_task takes no arguments, so it cannot declare a queue
    for keyword in decorator.keywords:
        if keyword.arg == "queue":
            return ast.unparse(keyword.value)
    return None


def _tasks_in_module(path: Path) -> list[tuple[str, str, str | None]]:
    """Every task-decorated function defined in a single module."""
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if _is_task_decorator(decorator):
                location = f"{path.relative_to(APPS_DIR.parent)}:{node.lineno}"
                found.append((location, node.name, _declared_queue(decorator)))
    return found


@cache
def _source_tasks() -> list[tuple[str, str, str | None]]:
    """Every task defined under ``apps/`` as ``(location, function name, declared queue)``.

    Cached: several tests below each need this, and re-parsing the whole ``apps/`` tree per
    test is pure waste since the source doesn't change within a test run.
    """
    found = []
    for path in sorted(APPS_DIR.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        found.extend(_tasks_in_module(path))
    return found


@cache
def _registered_tasks() -> dict[str, object]:
    """Cached for the same reason as ``_source_tasks``: rebuilt on every call otherwise,
    including once per parametrized case in ``test_router_sends_task_to_expected_queue``.
    """
    current_app.loader.import_default_modules()
    return {name: task for name, task in current_app.tasks.items() if name.startswith("apps.")}


def test_source_scan_finds_tasks():
    """Guard the guard: a broken scan would make every check below vacuously pass."""
    assert len(_source_tasks()) > 40


def test_every_task_declares_an_explicit_queue():
    undeclared = [f"{location} {name}" for location, name, queue in _source_tasks() if queue is None]
    assert not undeclared, (
        "These Celery tasks don't declare a queue, so they fall back to the chat queue and will "
        "compete with inbound messages. Add queue=Queues.<CHAT|BACKGROUND|EVALUATIONS>:\n" + "\n".join(undeclared)
    )


def test_declared_queues_use_the_queues_enum():
    valid = {f"Queues.{member.name}" for member in Queues}
    wrong = [f"{location} {name} -> {queue}" for location, name, queue in _source_tasks() if queue not in valid]
    assert not wrong, (
        f"Celery tasks must route via the {sorted(valid)} constants rather than a bare string, so "
        "that renaming a queue is a single edit:\n" + "\n".join(wrong)
    )


def test_registered_task_queues_are_declared_in_settings():
    declared = {queue.name for queue in settings.CELERY_TASK_QUEUES}
    # getattr default: an un-annotated task has no `queue` attribute at all, and should be
    # reported here rather than blowing up with an AttributeError.
    unroutable = {
        name: getattr(task, "queue", None)
        for name, task in _registered_tasks().items()
        if str(getattr(task, "queue", None)) not in declared
    }
    assert not unroutable, (
        f"These tasks route to queues absent from CELERY_TASK_QUEUES ({sorted(declared)}), so no "
        f"worker would consume them: {unroutable}"
    )


def test_all_queues_are_declared_in_settings():
    """A worker started without ``-Q`` consumes exactly the queues declared here."""
    assert {queue.name for queue in settings.CELERY_TASK_QUEUES} == {member.value for member in Queues}


@pytest.mark.parametrize(
    ("task_name", "expected_queue"),
    [
        pytest.param("apps.channels.tasks.handle_telegram_message", Queues.CHAT, id="inbound-message"),
        pytest.param("apps.events.tasks.poll_scheduled_messages", Queues.CHAT, id="event-polling"),
        pytest.param("apps.documents.tasks.index_collection_files_task", Queues.BACKGROUND, id="indexing"),
        pytest.param("apps.evaluations.tasks.evaluate_message_batch", Queues.EVALUATIONS, id="eval-fan-out"),
        # The eval control plane stays off the queue it drains, so a saturated evaluations queue
        # can never block the tasks responsible for draining it.
        pytest.param("apps.evaluations.tasks.coordinate_evaluation_runs", Queues.BACKGROUND, id="eval-coordinator"),
        pytest.param("apps.evaluations.tasks.drive_evaluation_run", Queues.BACKGROUND, id="eval-driver"),
        pytest.param("apps.evaluations.tasks.finalize_evaluation_run", Queues.BACKGROUND, id="eval-finalizer"),
    ],
)
def test_router_sends_task_to_expected_queue(task_name, expected_queue):
    """End-to-end through Celery's router, which is what actually decides the destination.

    ``Task.apply_async`` merges ``_get_exec_options()`` (where the decorator's ``queue=`` ends up)
    into the options it hands the router, so routing with anything less than that answers a
    different question — an empty dict resolves every task to the default queue.
    """
    task = _registered_tasks()[task_name]
    route = current_app.amqp.router.route(task._get_exec_options(), task_name)
    assert route["queue"].name == expected_queue


def test_unrouted_tasks_land_on_a_consumed_queue():
    """Third-party tasks can't carry our decorator, so the default must be a queue we consume."""
    assert current_app.conf.task_default_queue == Queues.CHAT
