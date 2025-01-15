class PipelineBuildError(Exception):
    """Exception to raise for errors detected at build time."""

    def __init__(self, message: str, node_id: str = None, edge_ids: list[str] = None):
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
