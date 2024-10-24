import factory

from apps.pipelines.models import Node, Pipeline, PipelineChatHistory, PipelineChatHistoryTypes
from apps.utils.factories.team import TeamFactory


class NodeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Node

    flow_id = factory.Faker("uuid4")
    type = "Passthrough"
    label = "Passthrough"
    params = {}
    pipeline = factory.SubFactory("apps.utils.factories.pipelines.PipelineFactory")


class PipelineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Pipeline
        skip_postgeneration_save = True

    name = "Test Pipeline"
    data = {
        "edges": [
            {
                "id": "1->2",
                "source": "first",
                "target": "second",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
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

    @factory.post_generation
    def nodes(self, create, *args, **kwargs):
        if not create:
            return

        NodeFactory(pipeline=self, flow_id="first")
        NodeFactory(pipeline=self, flow_id="second")


class PipelineChatHistoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PipelineChatHistory

    type = PipelineChatHistoryTypes.NAMED
    name = "name"
