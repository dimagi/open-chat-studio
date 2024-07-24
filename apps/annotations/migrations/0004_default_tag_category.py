from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('annotations', '0003_alter_tag_unique_together_tag_category_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            "UPDATE annotations_tag SET category = 'bot_response' WHERE is_system_tag = true",
            "UPDATE annotations_tag SET category = '' WHERE is_system_tag = true",
            elidable=True
        )
    ]
