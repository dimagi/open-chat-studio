# Pipeline Node Types

`NodeType` (`apps/pipelines/node_type.py`) is the one place that answers questions a node's `type`
string decides: which class it names, what params it declares, what handles it offers, how the
editor renders it. Ask it. Do not work the answer out again somewhere else.

Get one from whatever you are holding:

* `node.node_type` — a stored `Node` row
* `flow_node_data.node_type` — an unwritten edit on the wire (`FlowNodeData`)
* `NodeType(type_string)` — a bare type string

## Add the question to `NodeType`, don't fetch and interrogate

A function that takes a node type and then resolves the class itself is a second place answering
the same question, and the two drift:

```python
# No: the caller re-derives what the type means.
def is_router(node_type: str) -> bool:
    node_class = resolve_node_class(node_type)
    return node_class is not None and issubclass(node_class, PipelineRouterNode)
```

```python
# Yes: one accessor on NodeType, read as node.node_type.is_router
@property
def is_router(self) -> bool:
    node_class = self.node_class
    return node_class is not None and issubclass(node_class, _nodes_base().PipelineRouterNode)
```

Signs you are re-deriving:

* calling `resolve_node_class()` outside `node_type.py`
* testing a param with `node_class.model_fields` instead of `declares()` / `declared_params`
* a module-level dict or set keyed by node type — put it behind an accessor, or derive it from the
  node schemas the way `server_managed_node_types()` does

## What belongs where

* `NodeType` holds the type string and nothing else. Row questions — a node's name, params, tools —
  stay on `Node`. An accessor that needs params takes them as arguments, as
  `output_handles(params, node_id)` does. Params as fields would make it a second source of truth
  for what a node holds (ADR-0046)
* It is a null object. A type naming no node class is a supported state (`REMOVED_NODE_TYPES`, and
  nothing validates `Node.type` against the classes), so every accessor answers for one — empty,
  `None` or `False` — rather than raising; `exists` is the explicit question. A new accessor keeps
  that, and `TestNullObject` in `apps/pipelines/tests/test_node_type.py` pins the whole surface
* Nothing in `node_type.py` imports `apps.pipelines.nodes` at module level. That import pulls in
  langgraph and `apps.experiments.models`, and keeping it out is what lets `models.py`, `flow.py`,
  `versioning.py` and the API modules share these answers. Reach the node classes through the lazy
  `_nodes_base()` helper
