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

    if pipeline_group:
        if experiment_group:
            for membership in Membership.objects.filter(groups=pipeline_group):
                membership.groups.add(experiment_group)
                membership.groups.remove(pipeline_group)

            for invitation in Invitation.objects.filter(groups=pipeline_group):
                invitation.groups.add(experiment_group)
                invitation.groups.remove(pipeline_group)

        # Use raw SQL to avoid Django ORM cascading into the dropped
        # waffle_flag_groups table (dropped in migration 0008).
        schema_editor.execute("DELETE FROM auth_group_permissions WHERE group_id = %s", [pipeline_group.id])
        schema_editor.execute("DELETE FROM auth_group WHERE id = %s", [pipeline_group.id])

    if experiment_group:
        experiment_group.name = "Chatbot Admin"
        experiment_group.save(update_fields=["name"])


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "0008_drop_unused_waffle_tables"),
    ]

    operations = [
        migrations.RunPython(merge_and_rename, migrations.RunPython.noop),
    ]
