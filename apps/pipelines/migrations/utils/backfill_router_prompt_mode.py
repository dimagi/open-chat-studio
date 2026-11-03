"""Store ``prompt_mode: "custom"`` on LLM Router nodes saved before prompt modes existed.

Such a router sends the prompt it was written with; without a stored mode it would get the field
default, ``standard``. Migration ``pipelines.0036_backfill_router_prompt_mode`` calls this with the
historical ``Node`` model, so it uses ``_base_manager`` (which includes archived nodes) and plain
column access.

The migration runs while the previous release serves requests, so each batch is one ``UPDATE`` that
rechecks the missing mode and merges in only the ``prompt_mode`` key.
"""

from django.db.models import F, Func, JSONField, Q, Value

BATCH_SIZE = 1000


def backfill_router_prompt_mode(Node) -> int:
    routers = Node._base_manager.filter(type="RouterNode").filter(
        Q(params__prompt_mode__isnull=True) | Q(params__prompt_mode=None) | Q(params__prompt_mode="")
    )
    with_custom_mode = Func(
        F("params"),
        Value({"prompt_mode": "custom"}, output_field=JSONField()),
        template="%(expressions)s",
        arg_joiner=" || ",
        output_field=JSONField(),
    )
    updated = 0
    last_id = 0
    while ids := list(routers.filter(id__gt=last_id).order_by("id").values_list("id", flat=True)[:BATCH_SIZE]):
        updated += routers.filter(id__in=ids).update(params=with_custom_mode)
        last_id = ids[-1]
    return updated
