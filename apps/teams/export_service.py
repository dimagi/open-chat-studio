"""Which chatbots migration mode stops from firing.

An empty allowlist still means "everything", so a migrating team with no selection freezes all of
its chatbots, while one with a selection freezes only the listed families.
"""

from django.db.models import Q

from apps.experiments.models import Experiment

from .export.selection import family_q
from .models import Team


def frozen_experiment_q() -> Q:
    """Experiments whose outbound firing is frozen by migration mode.

    Callers filter a model with an ``experiment`` FK and apply it with ``.exclude(frozen_experiment_q())``.
    It is one column predicate over a lazy subquery, so the exclude adds no join; these run per
    conversation event and every ten seconds install-wide.
    """
    allowlist = Team.exportable_experiments.through.objects.filter(team__is_migrating=True)
    team_wide_ids = Team.objects.filter(is_migrating=True).exclude(id__in=allowlist.values("team_id")).values("id")
    # Triggers are versioned with their chatbot, so a trigger on a published version points at that
    # version's row rather than the working version the allowlist holds.
    frozen_ids = Experiment._base_manager.filter(
        Q(team_id__in=team_wide_ids) | family_q(allowlist.values("experiment_id"))
    ).values("id")
    return Q(experiment_id__in=frozen_ids)
