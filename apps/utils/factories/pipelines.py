import factory

from apps.pipelines.models import Pipeline
from apps.utils.factories.team import TeamFactory


class PipelineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Pipeline

    name = "Test Pipeline"
    data = {
        "edges": [
            {"id": "1->2", "source": "first", "target": "second"},
        ],
        "nodes": [
            {
                "id": "1",
                "data": {
                    "id": "first",
                    "type": "Passthrough",
                    "label": "Passthrough",
                    "params": {},
                },
            },
            {
                "id": "2",
                "data": {
                    "id": "second",
                    "type": "Passthrough",
                    "label": "Passthrough",
                    "params": {},
                },
            },
        ],
    }

    team = factory.SubFactory(TeamFactory)
