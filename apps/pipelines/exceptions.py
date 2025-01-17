class PipelineBuildError(Exception):
    """Exception to raise for errors detected at build time."""

    def __init__(self, message: str, node_id: str = None, edge_ids: list[str] = None):
        """
        Initialize a PipelineBuildError with detailed error information.
        
        Parameters:
            message (str): A descriptive error message explaining the pipeline build failure.
            node_id (str, optional): Identifier of the specific node where the error occurred. Defaults to None.
            edge_ids (list[str], optional): List of edge identifiers related to the error. Defaults to None.
        
        Attributes:
            message (str): Stores the error description.
            node_id (str): Stores the node identifier, if provided.
            edge_ids (list[str]): Stores the related edge identifiers, if provided.
        """
        self.message = message
        self.node_id = node_id
        self.edge_ids = edge_ids

    def to_json(self):
        """
        Convert the error details to a structured JSON-like dictionary.
        
        If a node_id is provided, returns a dictionary with the node details and its error message.
        Otherwise, returns a dictionary with a general pipeline error message.
        
        Returns:
            dict: A dictionary containing error details, with either node-specific or pipeline-level information.
                  Includes an optional list of edge IDs associated with the error.
        """
        if self.node_id:
            return {"node": {self.node_id: {"root": self.message}}, "edge": self.edge_ids}
        return {"pipeline": self.message, "edge": self.edge_ids}


class PipelineNodeBuildError(Exception):
    """Exception to raise for errors related to bad parameters or
    missing attributes that are detected during at runtime"""

    pass


class PipelineNodeRunError(Exception):
    pass
