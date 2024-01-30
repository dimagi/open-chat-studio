from inspect import isfunction

import factory
from django.contrib.auth.models import Group

from apps.teams.backends import NORMAL_USER_GROUPS, get_team_owner_groups
from apps.teams.models import Membership, Team
from apps.utils.factories.user import UserFactory


class TeamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Team
        skip_postgeneration_save = True

    name = factory.Faker("text", max_nb_chars=20)
    slug = factory.Sequence(lambda x: f"team-{x}")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership
        skip_postgeneration_save = True

    user = factory.SubFactory(UserFactory)
    team = factory.SubFactory(TeamFactory)
    role = "admin"

    @factory.post_generation
    def groups(self, create, extracted, **kwargs):
        if not create or not extracted:
            return

        if isfunction(extracted):
            extracted = extracted()

        self.groups.add(*extracted)


def get_test_user_groups():
    return list(Group.objects.filter(name__in=NORMAL_USER_GROUPS))


class TeamWithUsersFactory(TeamFactory):
    admin = factory.RelatedFactory(MembershipFactory, "team", role="admin", groups=get_team_owner_groups)
    member = factory.RelatedFactory(MembershipFactory, "team", role="member", groups=get_test_user_groups)
