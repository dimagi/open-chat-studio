from typing import TypedDict


class ErrorReport(TypedDict):
    """Everything wrong with a pipeline, bucketed by what the problem is attached to.

    ``node`` maps a node's flow id to its ``{field: message}`` errors, with non-field problems under
    the ``"root"`` sentinel. ``edge`` holds the ids of offending edges. ``pipeline`` holds messages
    that name no single node or edge. All three keys are always present; a report with all three
    empty means the pipeline is valid.
    """

    node: dict[str, dict[str, str]]
    edge: list[str]
    pipeline: list[str]


class MissingNodeDataError(ValueError):
    """A saved graph contains node ids with no provided content and no existing Node row."""

    def __init__(self, node_ids):
        self.node_ids = sorted(node_ids)
        super().__init__(f"No node data provided for new node(s): {self.node_ids}")


class PipelineBuildError(Exception):
    """Exception to raise for errors detected at build time."""

    def __init__(self, message: str, node_id: str | None = None, edge_ids: list[str] | None = None):
        """
        Parameters:
            message (str): A descriptive error message explaining the pipeline build failure.
            node_id (str, optional): Identifier of the specific node where the error occurred. Defaults to None.
            edge_ids (list[str], optional): List of edge identifiers related to the error. Defaults to None.
        """
        super().__init__(message)
        self.message = message
        self.node_id = node_id
        self.edge_ids = edge_ids

    def to_json(self):
        if self.node_id:
            return {"node": {self.node_id: {"root": self.message}}, "edge": self.edge_ids}
        return {"pipeline": self.message, "edge": self.edge_ids}


def has_errors(report: ErrorReport) -> bool:
    """Whether a report holds anything. All three buckets empty means the pipeline is valid."""
    return any(report.values())


def error_report(node_errors: dict, build_errors: list["PipelineBuildError"]) -> ErrorReport:
    """The three-bucket report: per-node field errors merged with graph-level build errors.

    A build error carrying a ``node_id`` is attributed to that node under the ``root`` sentinel,
    alongside any field errors it already has; the rest are graph-level and collect in ``pipeline``.
    ``edge_ids`` accumulate from every error, since an edge id identifies the offending edge whichever
    check produced it.
    """
    node = {flow_id: dict(fields) for flow_id, fields in node_errors.items()}
    edge: list[str] = []
    pipeline: list[str] = []
    for error in build_errors:
        if error.node_id:
            # One "root" per node: two graph-level errors naming the same node isn't a shape the
            # checks produce today.
            node.setdefault(error.node_id, {})["root"] = error.message
        else:
            pipeline.append(error.message)
        edge.extend(error.edge_ids or [])
    return {"node": node, "edge": edge, "pipeline": pipeline}


class PipelineNodeBuildError(Exception):
    """Exception to raise for errors related to bad parameters or
    missing attributes that are detected during at runtime"""

    pass


class PipelineNodeRunError(Exception):
    """A node failed while running.

    This is the general runtime failure for a node, and it is *not* swallowed by the message
    processing pipeline: some of its raise sites are genuine system bugs (an unset repository,
    an input the graph cannot resolve, a provider that fails to initialise), so it must keep
    reaching Sentry. Raise the ``CodeNodeRunError`` subclass instead when the cause is code the
    user wrote.
    """


class CodeNodeRunError(PipelineNodeRunError):
    """User-authored code in a CodeNode raised.

    A subclass rather than a sibling so that anything catching ``PipelineNodeRunError`` (the
    pipeline-test task) also catches user code errors, while the handlers that must distinguish
    a user's mistake from a system bug -- ``apps.channels.pipeline``, which answers with a canned
    reply and does not re-raise, and ``apps.trace.error_parser``, which tags the trace -- can
    still catch this narrower type.
    """


class WaitForNextInput(Exception):
    """Exception to raise when a node is waiting for input from specific upstream nodes.

    This exception is handled by the pipeline execution framework to pause execution
    until required dependencies (other nodes) have completed their execution.

    Example:
        raise WaitForNextInput() when a node requires outputs from specific upstream nodes
        that haven't executed yet.
    """


class AbortPipeline(Exception):
    """Exception to raise when the pipeline should be aborted.

    This exception is used to stop the pipeline execution and can be caught by the pipeline runner.
    """

    def __init__(self, message: str, tag_name: str | None = None):
        """
        Parameters:
            message (str): A descriptive error message explaining the reason for the abortion.
        """
        super().__init__(message)
        self.message = message
        self.tag_name = tag_name

    def to_json(self):
        return {"message": self.message, "tag_name": self.tag_name}
