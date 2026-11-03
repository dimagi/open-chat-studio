"""Store ``prompt_mode: "custom"`` on LLM Router nodes saved before prompt modes existed.

Such a router sends the prompt it was written with; without a stored mode it would get the field
default, ``standard``. Migration ``pipelines.0036_backfill_router_prompt_mode`` calls this with the
historical ``Node`` model, so it uses ``_base_manager`` (which includes archived nodes) and plain
column access.
"""

from django.db.models import Q

BATCH_SIZE = 1000


def backfill_router_prompt_mode(Node) -> int:
    routers = Node._base_manager.filter(type="RouterNode").filter(
        Q(params__prompt_mode__isnull=True) | Q(params__prompt_mode=None) | Q(params__prompt_mode="")
    )
    node_ids = list(routers.values_list("id", flat=True))
    for start in range(0, len(node_ids), BATCH_SIZE):
        batch = list(Node._base_manager.filter(id__in=node_ids[start : start + BATCH_SIZE]).only("id", "params"))
        for node in batch:
            node.params["prompt_mode"] = "custom"
        Node._base_manager.bulk_update(batch, ["params"])
    return len(node_ids)
