import factory
import factory.django
import faker
from django.conf import settings

fake = faker.Faker()


class FileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "files.File"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
    name = factory.Sequence(lambda _: fake.unique.file_name())
    file = factory.django.FileField(filename=factory.Faker("file_name"))
    content_type = "text/plain"


class FileChunkEmbeddingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "files.FileChunkEmbedding"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
    file = factory.SubFactory(FileFactory, team=factory.SelfAttribute("..team"))
    collection = factory.SubFactory(
        "apps.utils.factories.documents.CollectionFactory", team=factory.SelfAttribute("..team")
    )
    chunk_number = factory.Sequence(lambda n: n)
    text = "chunk text"
    page_number = 1
    embedding = factory.LazyFunction(lambda: [0.0] * settings.EMBEDDING_VECTOR_SIZE)
