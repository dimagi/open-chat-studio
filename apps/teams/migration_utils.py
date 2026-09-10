"""Helpers for `teams` data migrations."""

from waffle import get_waffle_flag_model


def delete_waffle_flag(flag_model, flag_name: str) -> None:
    """Delete a waffle flag row and drop the flag from waffle's cache.

    `flag_model` is the historical model a migration receives, i.e.
    `apps.get_model("teams", "Flag")`. The flush runs on the concrete model, which alone
    has the method; its keys derive from the flag name, so an unsaved instance can
    compute them.
    """
    flag_model.objects.filter(name=flag_name).delete()
    get_waffle_flag_model()(name=flag_name).flush()
