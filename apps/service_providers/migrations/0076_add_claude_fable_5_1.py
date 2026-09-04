from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0075_alter_embeddingprovidermodel_type_and_more"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0001_initial"),
    ]

    operations = [
        # load_pricing_data() moved to 0078_add_gemini_3_8_flash so it runs only once per deploy
        # (the newest migration loads the whole seed file, which covers claude-fable-5-1 too).
    ]
