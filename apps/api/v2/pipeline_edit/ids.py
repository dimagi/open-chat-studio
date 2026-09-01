"""How the façade draws an id for something it creates (#4140, #4141).

Nodes and edges start from different bases -- a node id is its type, an edge id is react-flow's own
formula over the wiring -- but the draw is the same either way, and so is the reason it is bounded, so
both live here rather than in whichever module happened to need one first.
"""

from uuid import uuid4

#: Length of the short random suffix, matching the ``short-unique-id`` length the UI builder's own
#: ``getNodeId`` uses. Five hex characters is about a million ids: plenty per pipeline, but not so
#: many that a collision can be waved away, since ``apply_pipeline_patch`` treats an add whose id
#: already exists as a no-op and would answer 201 having stored nothing.
SHORT_ID_LENGTH = 5

#: How many draws are spent looking for a free id before falling back to a full-length uuid.
ID_ATTEMPTS = 5


def with_free_suffix(base: str, taken: set[str]) -> str:
    """``base`` plus a short suffix no id in ``taken`` has, or a full uuid once the draws run out.

    Bounded rather than looping: this runs inside the pipeline row lock, so an exhausted or degenerate
    id source must not spin there until the request times out.
    """
    for _attempt in range(ID_ATTEMPTS):
        candidate = f"{base}-{uuid4().hex[:SHORT_ID_LENGTH]}"
        if candidate not in taken:
            return candidate
    return f"{base}-{uuid4().hex}"
