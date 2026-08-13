"""Team-scoped resolution of the resources the v2 endpoints address by id.

One definition of "which chatbot does this request mean" for every v2 view, read or write: the
tenancy predicate is the security boundary, so it is written once rather than once per view.
"""

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from apps.experiments.models import Experiment


def working_chatbots(team) -> QuerySet[Experiment]:
    """The team's working (draft) chatbots.

    Version snapshots are excluded because writes only ever target the working version, and the
    default manager already excludes archived rows.
    """
    return Experiment.objects.filter(team=team, working_version__isnull=True)


def get_working_chatbot(team, public_id, *, lock: bool = False) -> Experiment:
    """The team's working chatbot named by ``public_id``, or ``Http404``.

    ``lock`` takes a row lock for the rest of the transaction, which a writer needs and a reader
    does not.
    """
    queryset = working_chatbots(team)
    if lock:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, public_id=public_id)
