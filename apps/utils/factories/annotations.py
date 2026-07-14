import factory
import factory.django
from django.contrib.contenttypes.models import ContentType

from apps.annotations.models import CustomTaggedItem, Tag, UserComment
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class TagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tag

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"tag-{n}")


class UserCommentFactory(factory.django.DjangoModelFactory):
    """A comment attached (generic FK) to a chat message by default."""

    class Meta:
        model = UserComment
        exclude = ["target"]

    team = factory.SubFactory(TeamFactory)
    user = factory.SubFactory(UserFactory)
    comment = factory.Sequence(lambda n: f"comment {n}")

    object_id = factory.SelfAttribute("target.id")
    content_type = factory.LazyAttribute(lambda obj: ContentType.objects.get_for_model(obj.target))

    class Params:
        target = factory.SubFactory(ChatMessageFactory, chat=factory.SubFactory(ChatFactory))


class CustomTaggedItemFactory(factory.django.DjangoModelFactory):
    """A tag applied (generic FK) to a chat message by default."""

    class Meta:
        model = CustomTaggedItem
        exclude = ["target"]

    team = factory.SubFactory(TeamFactory)
    user = factory.SubFactory(UserFactory)
    tag = factory.SubFactory(TagFactory)

    object_id = factory.SelfAttribute("target.id")
    content_type = factory.LazyAttribute(lambda obj: ContentType.objects.get_for_model(obj.target))

    class Params:
        target = factory.SubFactory(ChatMessageFactory, chat=factory.SubFactory(ChatFactory))
