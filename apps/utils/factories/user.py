import factory

from apps.users.models import CustomUser


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomUser

    username = factory.Faker("email")
    password = factory.PostGenerationMethodCall("set_password", "password")
