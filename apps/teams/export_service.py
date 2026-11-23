"""Which chatbots migration mode or a pending team deletion stops from firing.

An empty allowlist still means "everything", so a migrating team with no selection freezes all of
its chatbots, while one with a selection freezes only the listed families. A team scheduled for
deletion freezes all of its chatbots.
"""

from django.db.models import Q

from apps.experiments.models import Experiment

from .export.selection import family_q
from .models import Team


def frozen_experiment_q() -> Q:
    """Experiments whose outbound firing is frozen by migration mode or a pending team deletion.

    Callers filter a model with an ``experiment`` FK and apply it with ``.exclude(frozen_experiment_q())``.
    It is one column predicate over a lazy subquery, so the exclude adds no join; these run per
    conversation event and every ten seconds install-wide.
    """
    allowlist = Team.exportable_experiments.through.objects.filter(team__is_migrating=True)
    team_wide_ids = Team.objects.filter(
        (Q(is_migrating=True) & ~Q(id__in=allowlist.values("team_id"))) | Q(deletion_requested_at__isnull=False)
    ).values("id")
    # Triggers are versioned with their chatbot, so a trigger on a published version points at that
    # version's row rather than the working version the allowlist holds.
    frozen_ids = Experiment._base_manager.filter(
        Q(team_id__in=team_wide_ids) | family_q(allowlist.values("experiment_id"))
    ).values("id")
    return Q(experiment_id__in=frozen_ids)
