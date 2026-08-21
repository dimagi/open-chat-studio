"""Team-scoped resolution of the resources the v2 endpoints address by id.

One definition of "which chatbot does this request mean" for every v2 view, read or write: the
tenancy predicate is the security boundary, so it is written once rather than once per view.
"""

from django.db.models import QuerySet

# DRF's ``get_object_or_404`` below, not Django's: a chatbot ``public_id`` is a ``UUIDField`` and the
# router's lookup regex admits any non-slash string, so a malformed id reaches
# ``UUIDField.to_python`` and raises ``ValidationError``. Django's helper catches only
# ``DoesNotExist`` and would let that surface as a 500; DRF's also catches ``TypeError``,
# ``ValueError`` and ``ValidationError``.
from rest_framework.generics import get_object_or_404

from apps.experiments.models import Experiment
from apps.teams.models import Team
from apps.teams.utils import get_current_team


def request_team(request) -> Team | None:
    """The team the credential authenticated with.

    Not simply ``request.team``: the auth layer sets that on the *DRF* request, and DRF's OPTIONS
    metadata re-runs the permission checks against a ``clone_request``, which wraps the same Django
    request but does not copy that attribute. There ``request.team`` falls through to the
    middleware's lazy lookup, which is ``None`` on an API path -- and ``DjangoModelPermissions``
    calls ``get_queryset()`` with it, so the read raises rather than 404s. The auth layer also calls
    ``set_current_team``, so fall back to that.
    """
    return request.team or get_current_team()


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
