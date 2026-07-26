from django.db import migrations


def remove_stale_span_contenttype(apps, schema_editor):
    """Delete the leftover `trace.span` content type.

    Span was a taggable model until it was dropped in 0008 with a bare DeleteModel, which left its
    content type and every row pointing at it (tags, comments, auto-created permissions) behind.
    Those dangling rows break the team-export sync: it resolves each generic FK's content type by
    natural key on the target, and `trace.span` no longer resolves there. Deleting the content type
    takes its CASCADE dependents with it (Django emulates the cascade in Python, so every referencing
    app must be in this migration's dependencies). No-op once the content type is gone.
    """
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="trace", model="span").delete()


class Migration(migrations.Migration):
    # Every app with a FK into ContentType that could reference `trace.span` must be applied before
    # this runs, so the cascade sees and removes its dangling rows rather than hitting an FK error.
    dependencies = [
        ("trace", "0013_trace_trace_timestamp_idx"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("admin", "0003_logentry_add_action_flag_choices"),
        ("taggit", "0006_rename_taggeditem_content_type_object_id_taggit_tagg_content_8fc721_idx"),
        ("annotations", "0009_alter_tag_category"),
        ("assessments", "0001_initial"),
        ("events", "0027_timeouttrigger_config_changed_at"),
    ]

    operations = [
        migrations.RunPython(remove_stale_span_contenttype, migrations.RunPython.noop),
    ]
