import factory

from apps.trace.models import Span, Trace


class TraceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Trace


class SpanFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Span
