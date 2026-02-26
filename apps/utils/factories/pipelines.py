import factory
import factory.django

from apps.pipelines.models import Node, Pipeline, PipelineChatHistory, PipelineChatHistoryTypes
from apps.pipelines.nodes.nodes import EndNode, StartNode
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
                "source": "start",
                "target": "end",
            },
        ],
        "nodes": [
            {
                "id": "start",
                "data": {
                    "id": "start",
                    "type": StartNode.__name__,
                },
            },
            {
                "id": "end",
                "data": {
                    "id": "end",
                    "type": EndNode.__name__,
                },
            },
        ],
    }

    team = factory.SubFactory(TeamFactory)

    @factory.post_generation
    def nodes(self, create, *args, **kwargs):
        if not create:
            return
        self.update_nodes_from_data()


class PipelineChatHistoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PipelineChatHistory

    type = PipelineChatHistoryTypes.NAMED
    name = "name"
