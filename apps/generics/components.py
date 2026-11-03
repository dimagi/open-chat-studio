"""Render cotton components from Python code, where the `<c-...>` template syntax is not available."""

from functools import cache

from django.template import engines


@cache
def _component_template(name: str, attr_names: tuple[str, ...]):
    attrs = " ".join(f':{attr}="{attr}"' for attr in attr_names)
    return engines["django"].from_string(f"{{% cotton {name} {attrs} %}}{{{{ slot }}}}{{% endcotton %}}")


def render_component(name: str, slot: str = "", **attrs) -> str:
    """Render the component `name` (for example "ui.chip") with `attrs` as its attributes and `slot` as its content.

    `slot` is escaped unless it is marked safe.
    """
    return _component_template(name, tuple(attrs)).render({"slot": slot, **attrs})
