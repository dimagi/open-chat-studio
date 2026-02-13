import factory

from apps.human_annotations.models import AnnotationQueue, AnnotationSchema
from apps.utils.factories.team import TeamFactory


class AnnotationSchemaFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnnotationSchema

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Schema {n}")
    schema = factory.LazyFunction(
        lambda: {
            "quality_score": {"type": "int", "description": "Overall quality 1-5", "ge": 1, "le": 5},
            "notes": {"type": "string", "description": "Additional notes"},
        }
    )


class AnnotationQueueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnnotationQueue

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Queue {n}")
    schema = factory.SubFactory(AnnotationSchemaFactory, team=factory.SelfAttribute("..team"))
    created_by = factory.LazyAttribute(lambda obj: obj.team.members.first())
    num_reviews_required = 1
