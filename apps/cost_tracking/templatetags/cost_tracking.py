"""Template filters for cost-tracking dashboard surfaces."""

from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def cost_display(value) -> str:
    """Render a Decimal cost: 2 decimal places for $0.01+, 4 for sub-cent.

    The dashboard's default `floatformat:2` flattens any spend below half
    a cent to "$0.00", which hides real early-usage and demo activity.
    This picks per-magnitude precision so the same field reads naturally
    at any scale.
    """
    if value is None:
        return "0.00"
    value = Decimal(str(value))
    if value == 0 or value >= Decimal("0.01"):
        return f"{value:.2f}"
    return f"{value:.4f}"
