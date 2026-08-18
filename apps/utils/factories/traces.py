import factory
import factory.django

from apps.trace.models import Trace


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
