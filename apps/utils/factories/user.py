import factory
from django.contrib.auth.models import Group

from apps.users.models import CustomUser


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomUser

    username = factory.Faker("email")
    password = factory.PostGenerationMethodCall("set_password", "password")


class GroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Group

    name = factory.Faker("color_name")
