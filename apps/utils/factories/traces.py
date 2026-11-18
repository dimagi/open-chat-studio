import factory
import factory.django

from apps.trace.models import CustomActionRequest, Trace


class TraceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Trace
        skip_postgeneration_save = True

    duration = 1000

    @factory.post_generation
    def at(self, create, extracted, **kwargs):
        """Stamp `timestamp` to a specific moment after creation. Skips when
        not provided; tests that don't care about timing get auto_now_add."""
        if not create or extracted is None:
            return
        Trace.objects.filter(pk=self.pk).update(timestamp=extracted)
        self.refresh_from_db()


class CustomActionRequestFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomActionRequest

    trace = factory.SubFactory(TraceFactory)
    team = factory.SelfAttribute("trace.team")
    operation = "weather_get"
    method = "GET"
    url = "https://api.example.com/weather"
    status_code = 200
    duration_ms = 100
