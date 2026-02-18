import factory
import factory.django
import faker
from django.contrib.auth.models import Group

from apps.users.models import CustomUser

fake = faker.Faker()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomUser
        skip_postgeneration_save = True

    # TODO: Replace when factory_boy supports `unique`.
    #  See https://github.com/FactoryBoy/factory_boy/pull/997
    username = factory.Sequence(lambda _: fake.unique.safe_email())
    email = factory.SelfAttribute("username")

    @factory.post_generation
    def set_password(self, create, extracted, **kwargs):
        if create:
            self.set_password(extracted or "password")
            self.save()


class GroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Group

    name = factory.Sequence(lambda n: f"Group {n}")
