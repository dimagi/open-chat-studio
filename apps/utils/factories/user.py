import factory
from django.contrib.auth.models import Group

from apps.users.models import CustomUser


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomUser
        skip_postgeneration_save = True

    username = factory.Faker("email")

    @factory.post_generation
    def set_password(self, create, extracted, **kwargs):
        if create:
            self.set_password(extracted or "password")
            self.save()


class GroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Group

    name = factory.Faker("color_name")
