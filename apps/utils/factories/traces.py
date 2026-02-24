import factory
import factory.django

from apps.trace.models import Trace


class TraceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Trace
