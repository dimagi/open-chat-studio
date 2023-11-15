import factory

from apps.teams.models import Membership, Team
from apps.utils.factories.user import UserFactory


class TeamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Team

    name = factory.Faker("text", max_nb_chars=20)
    slug = factory.Sequence(lambda x: f"team-{x}")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership

    user = factory.SubFactory(UserFactory)
    team = factory.SubFactory(TeamFactory)
    role = "admin"

    @factory.post_generation
    def groups(self, create, extracted, **kwargs):
        if not create or not extracted:
            return

        self.groups.add(*extracted)


class TeamWithUsersFactory(TeamFactory):
    admin = factory.RelatedFactory(MembershipFactory, "team", role="admin")
    member = factory.RelatedFactory(MembershipFactory, "team", role="member")
