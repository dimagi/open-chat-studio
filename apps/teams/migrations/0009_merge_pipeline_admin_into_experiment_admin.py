"""Merge 'Pipeline Admin' into 'Experiment Admin' and rename to 'Chatbot Admin'.

Any memberships that reference the 'Pipeline Admin' group are migrated to
'Experiment Admin', and the 'Pipeline Admin' group is deleted. Then
'Experiment Admin' is renamed to 'Chatbot Admin'. The permission changes
are handled by create_default_groups() which is called on every deploy.
"""

from django.db import migrations


def merge_and_rename(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Membership = apps.get_model("teams", "Membership")
    Invitation = apps.get_model("teams", "Invitation")

    experiment_group = Group.objects.filter(name="Experiment Admin").first()
    pipeline_group = Group.objects.filter(name="Pipeline Admin").first()
    chatbot_group = Group.objects.filter(name="Chatbot Admin").first()

    # Determine the destination group: use existing "Chatbot Admin" if it exists
    # (e.g. created by create_default_groups() running before this migration),
    # otherwise rename "Experiment Admin".
    if chatbot_group:
        dest_group = chatbot_group
    elif experiment_group:
        experiment_group.name = "Chatbot Admin"
        experiment_group.save(update_fields=["name"])
        dest_group = experiment_group
    else:
        dest_group = Group.objects.create(name="Chatbot Admin")

    # Migrate memberships and invitations from source groups into the destination.
    source_groups = [g for g in [experiment_group, pipeline_group] if g and g.pk != dest_group.pk]
    for source in source_groups:
        for membership in Membership.objects.filter(groups=source):
            membership.groups.add(dest_group)
            membership.groups.remove(source)
        for invitation in Invitation.objects.filter(groups=source):
            invitation.groups.add(dest_group)
            invitation.groups.remove(source)

    # Delete source groups via raw SQL to avoid Django ORM cascading into the
    # dropped waffle_flag_groups table (dropped in migration 0008).
    for source in source_groups:
        schema_editor.execute("DELETE FROM auth_group_permissions WHERE group_id = %s", [source.id])
        schema_editor.execute("DELETE FROM auth_group WHERE id = %s", [source.id])


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0008_drop_unused_waffle_tables"),
    ]

    operations = [
        migrations.RunPython(merge_and_rename, migrations.RunPython.noop),
    ]
