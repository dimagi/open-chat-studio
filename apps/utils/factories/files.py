import factory
import factory.django
import faker

fake = faker.Faker()


class FileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "files.File"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
    name = factory.Sequence(lambda _: fake.unique.file_name())
    file = factory.django.FileField(filename=factory.Faker("file_name"))
    content_type = "text/plain"
