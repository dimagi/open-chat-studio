"""What `Pipeline.validate()` does with a node it cannot parse at all.

It parses every node on every read, so an unparseable node has to be reportable: raising instead
would take `/inspect/` and every write to that pipeline down with it.
"""

import logging
from unittest.mock import Mock, patch

import pytest
from django.db import DatabaseError

from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.mark.django_db()
def test_a_param_of_the_wrong_python_type_is_reported(caplog):
    """`CodeNode.check_reserved_session_state_keys` runs `mode="before"` and regex-searches the
    value, so an integer raises `TypeError`, which pydantic does not wrap. The report says the node
    is unreadable; the exception goes to the log, since the report is served over the API."""
    pipeline = PipelineFactory.create()
    node = NodeFactory.create(pipeline=pipeline, type="CodeNode", params={"name": "broken", "code": 123})

    with caplog.at_level(logging.ERROR, logger="ocs.pipelines"):
        errors = pipeline.validate(full=False)

    assert errors["node"][node.flow_id] == {"root": "This node could not be read. Check the values of its params."}
    assert "TypeError" in caplog.text


@pytest.mark.django_db()
def test_a_database_error_is_not_swallowed():
    """Reporting one as a node error would leave the surrounding transaction aborted, so the next
    query raises with nothing left to say about where the failure came from."""
    pipeline = PipelineFactory.create()
    NodeFactory.create(pipeline=pipeline, type="CodeNode", params={"name": "code", "code": "pass"})
    unreachable = Mock(model_validate=Mock(side_effect=DatabaseError("connection lost")))

    with (
        patch("apps.pipelines.nodes.base.resolve_node_class", return_value=unreachable),
        pytest.raises(DatabaseError),
    ):
        pipeline.validate(full=False)
