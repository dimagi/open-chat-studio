import json

from django.conf import settings
from django.core.management import BaseCommand

from apps.pipelines.views import _pipeline_node_schemas


class Command(BaseCommand):
    def handle(self, *args, **options):
        base_path = settings.BASE_DIR / "apps" / "pipelines" / "tests" / "data"
        schemas = _pipeline_node_schemas()
        for schema in schemas:
            title = schema["title"]
            path = base_path / f"{title}.json"
            path.write_text(json.dumps(schema, indent=2))
