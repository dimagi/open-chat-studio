import factory

from apps.human_annotations.models import AnnotationSchema
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
