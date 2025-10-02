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

    # TODO: Replace when factory_boy supports `unique`.
    #   See https://github.com/FactoryBoy/factory_boy/pull/997
    name = factory.Sequence(lambda n: f"Team {n}")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership
        skip_postgeneration_save = True

    user = factory.SubFactory(UserFactory)
    team = factory.SubFactory(TeamFactory)

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
    admin = factory.RelatedFactory(MembershipFactory, "team", groups=get_team_owner_groups)
    member = factory.RelatedFactory(MembershipFactory, "team", groups=get_test_user_groups)
