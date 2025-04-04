import factory


class CollectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "documents.Collection"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
