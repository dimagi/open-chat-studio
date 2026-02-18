import factory

from apps.human_annotations.models import AnnotationItem, AnnotationQueue
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


class AnnotationQueueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnnotationQueue

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Queue {n}")
    schema = factory.LazyFunction(
        lambda: {
            "quality_score": {"type": "int", "description": "Overall quality 1-5", "ge": 1, "le": 5},
            "notes": {"type": "string", "description": "Additional notes"},
        }
    )
    created_by = factory.LazyAttribute(lambda obj: obj.team.members.first())
    num_reviews_required = 1


class AnnotationItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnnotationItem

    queue = factory.SubFactory(AnnotationQueueFactory)
    team = factory.SelfAttribute("queue.team")
    item_type = "session"
    session = factory.SubFactory(
        ExperimentSessionFactory,
        team=factory.SelfAttribute("..team"),
        chat__team=factory.SelfAttribute("..team"),
    )
