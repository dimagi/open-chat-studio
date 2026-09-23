import copy
from typing import cast

import factory
import factory.django

from apps.pipelines.flow import FullFlow, split_flow_data
from apps.pipelines.models import Node, Pipeline, PipelineChatHistory, PipelineChatHistoryTypes, PipelineChatMessages
from apps.pipelines.nodes.nodes import EndNode, StartNode
from apps.utils.factories.team import TeamFactory

_DEFAULT_PIPELINE_DATA = {
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
                # Named as `_get_start_and_end_nodes` names them. Without it both nodes read as
                # name=None and `Pipeline.validate()` reports a duplicate-name error.
                "params": {"name": "start"},
            },
        },
        {
            "id": "end",
            "data": {
                "id": "end",
                "type": EndNode.__name__,
                "params": {"name": "end"},
            },
        },
    ],
}


class NodeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Node
        skip_postgeneration_save = True

    flow_id = factory.Faker("uuid4")
    type = "Passthrough"
    label = "Passthrough"
    params = factory.LazyFunction(dict)
    pipeline = factory.SubFactory("apps.utils.factories.pipelines.PipelineFactory")

    @factory.post_generation
    def resource_fks(self, create, *args, **kwargs):
        """Derive the resource FK columns from ``params``, as the real write path does.

        A column the caller passed directly is overlaid onto the params the sync reads, so it
        wins while every other resource params names is still derived. The overlay is not
        persisted -- the sync saves the FK columns alone -- so a node built with columns but no
        ids in params keeps params free of them.
        """
        if not create:
            return
        # ``self`` is the created Node here, not the factory class whose ``params`` is a declaration.
        node = cast("Node", self)
        explicit = {
            f"{name}_id": value
            for name in Node.resource_fk_fields()
            if (value := getattr(node, f"{name}_id")) is not None
        }
        stored_params = node.params
        node.params = {**(stored_params or {}), **explicit}
        try:
            node._sync_resource_fk_fields()
        finally:
            node.params = stored_params


class PipelineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Pipeline
        skip_postgeneration_save = True

    name = "Test Pipeline"
    data = factory.LazyFunction(lambda: copy.deepcopy(_DEFAULT_PIPELINE_DATA))

    team = factory.SubFactory(TeamFactory)

    @factory.post_generation
    def nodes(self, create, *args, **kwargs):
        if not create:
            return
        # `self` is the created Pipeline instance here, not the factory class.
        layout, node_data = split_flow_data(FullFlow(**self.data))
        self.data = layout.model_dump()
        self.save(update_fields=["data"])
        self.update_nodes_from_data(node_data)


class PipelineChatHistoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PipelineChatHistory

    id = factory.Sequence(lambda n: n + 1)
    session = factory.SubFactory("apps.utils.factories.experiment.ExperimentSessionFactory")
    type = PipelineChatHistoryTypes.NAMED
    name = "name"


class PipelineChatMessagesFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PipelineChatMessages

    id = factory.Sequence(lambda n: n + 1)
    chat_history = factory.SubFactory(PipelineChatHistoryFactory)
    node_id = "test-node"
    human_message = "Hello"
    ai_message = "Hi there"
