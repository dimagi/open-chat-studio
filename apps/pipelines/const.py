STANDARD_OUTPUT_NAME: str = "output"  # The frontend defines names for each output. This is the default.
STANDARD_INPUT_NAME: str = "input"

#: The two node types the server owns rather than the builder: they are created with the pipeline,
#: cannot be added or deleted, and get their own react-flow types below.
START_NODE_TYPE: str = "StartNode"
END_NODE_TYPE: str = "EndNode"

#: React-flow node types. ``Node.type`` (the pipeline node class name) maps onto one of
#: these for the editor; the reserved start/end classes get their own types.
REACT_FLOW_START_TYPE = "startNode"
REACT_FLOW_END_TYPE = "endNode"
REACT_FLOW_NODE_TYPE = "pipelineNode"
