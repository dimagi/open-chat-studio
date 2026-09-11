from django.conf import settings
from django.db import migrations

GROUP_NAME = "Assistant Admin"


def delete_assistant_admin_group(apps, schema_editor):
    """Delete the group and every m2m row referencing it, leaving the memberships themselves.

    Raw SQL rather than an ORM delete, as in 0009: a cascade from auth_group reaches waffle's
    swapped-out default flag tables, which 0008 dropped from the database but not from state.
    """
    group = apps.get_model("auth", "Group").objects.filter(name=GROUP_NAME).first()
    if group is None:
        return

    holders = [apps.get_model("teams", model_name) for model_name in ("Membership", "Invitation", "Flag")]
    holders.append(apps.get_model(settings.AUTH_USER_MODEL))
    for holder in holders:
        through = holder._meta.get_field("groups").remote_field.through
        schema_editor.execute(f'DELETE FROM "{through._meta.db_table}" WHERE group_id = %s', [group.id])

    schema_editor.execute("DELETE FROM auth_group_permissions WHERE group_id = %s", [group.id])
    schema_editor.execute("DELETE FROM auth_group WHERE id = %s", [group.id])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("teams", "0017_team_require_mfa"),
    ]

    operations = [
        migrations.RunPython(delete_assistant_admin_group, migrations.RunPython.noop),
    ]
