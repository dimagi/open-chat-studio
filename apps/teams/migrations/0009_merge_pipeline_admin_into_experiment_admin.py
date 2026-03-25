"""Merge 'Pipeline Admin' group into 'Experiment Admin'.

Any memberships that reference the 'Pipeline Admin' group are migrated to
'Experiment Admin', and the 'Pipeline Admin' group is deleted. The permission
changes are handled by create_default_groups() which is called on every deploy.
"""

from django.db import migrations


def merge_pipeline_admin(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    pipeline_group = Group.objects.filter(name="Pipeline Admin").first()
    if not pipeline_group:
        return

    experiment_group = Group.objects.filter(name="Experiment Admin").first()
    if not experiment_group:
        # Shouldn't happen, but nothing to migrate to
        pipeline_group.delete()
        return

    Membership = apps.get_model("teams", "Membership")
    for membership in Membership.objects.filter(groups=pipeline_group):
        membership.groups.add(experiment_group)
        membership.groups.remove(pipeline_group)

    Invitation = apps.get_model("teams", "Invitation")
    for invitation in Invitation.objects.filter(groups=pipeline_group):
        invitation.groups.add(experiment_group)
        invitation.groups.remove(pipeline_group)

    pipeline_group.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "0008_drop_unused_waffle_tables"),
    ]

    operations = [
        migrations.RunPython(merge_pipeline_admin, migrations.RunPython.noop),
    ]
