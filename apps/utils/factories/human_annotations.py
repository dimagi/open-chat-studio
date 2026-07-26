import factory
import factory.django

from apps.human_annotations.models import Annotation, AnnotationItem, AnnotationQueue, AnnotationQueueAggregate
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


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


class AnnotationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Annotation

    team = factory.SubFactory(TeamFactory)
    item = factory.SubFactory(AnnotationItemFactory, team=factory.SelfAttribute("..team"))
    reviewer = factory.SubFactory(UserFactory)


class AnnotationQueueAggregateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnnotationQueueAggregate

    team = factory.SubFactory(TeamFactory)
    queue = factory.SubFactory(AnnotationQueueFactory, team=factory.SelfAttribute("..team"))
