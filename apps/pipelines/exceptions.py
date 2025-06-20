class PipelineBuildError(Exception):
    """Exception to raise for errors detected at build time."""

    def __init__(self, message: str, node_id: str = None, edge_ids: list[str] = None):
        """
        Parameters:
            message (str): A descriptive error message explaining the pipeline build failure.
            node_id (str, optional): Identifier of the specific node where the error occurred. Defaults to None.
            edge_ids (list[str], optional): List of edge identifiers related to the error. Defaults to None.
        """
        self.message = message
        self.node_id = node_id
        self.edge_ids = edge_ids

    def to_json(self):
        if self.node_id:
            return {"node": {self.node_id: {"root": self.message}}, "edge": self.edge_ids}
        return {"pipeline": self.message, "edge": self.edge_ids}


class PipelineNodeBuildError(Exception):
    """Exception to raise for errors related to bad parameters or
    missing attributes that are detected during at runtime"""

    pass


class PipelineNodeRunError(Exception):
    pass


class WaitForNextInput(Exception):
    """Exception to raise when a node is waiting for input from a specific node or set of nodes.

    This exception is handled by the node and used to alter the pipeline execution flow.
    """


class AbortPipeline(Exception):
    """Exception to raise when the pipeline should be aborted.

    This exception is used to stop the pipeline execution and can be caught by the pipeline runner.
    """

    def __init__(self, message: str, tag_name: str = None):
        """
        Parameters:
            message (str): A descriptive error message explaining the reason for the abortion.
        """
        super().__init__(message)
        self.message = message
        self.tag_name = tag_name

    def to_json(self):
        return {"message": self.message, "tag_name": self.tag_name}
