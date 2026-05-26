import factory
import factory.django
from django.contrib.contenttypes.models import ContentType

from apps.assessments.models import Score
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


class ScoreFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Score
        exclude = ["session"]

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"field_{n}")
    data_type = Score.DataType.CATEGORICAL
    value_string = "yes"
    source = Score.Source.LLM_JUDGE

    target_object_id = factory.SelfAttribute("session.id")
    target_content_type = factory.LazyAttribute(lambda obj: ContentType.objects.get_for_model(obj.session))

    class Params:
        session = factory.SubFactory(ExperimentSessionFactory, team=factory.SelfAttribute("..team"))
