import factory


class CollectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "documents.Repository"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
