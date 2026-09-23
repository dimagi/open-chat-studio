"""What a team's chatbot allowlist means.

One definition of "which chatbots may be picked" and "what picking one includes", shared by the
team settings form, the export scope, and the migration freeze.
"""

from collections.abc import Sequence

from django.db.models import Q, QuerySet

from apps.experiments.models import Experiment
from apps.teams.export.translation import selection_key
from apps.teams.models import Team

SELECTION_CHANGED_DETAIL = "The team's chatbot selection changed during the sync."


def _allowlist(team: Team) -> QuerySet:
    """The allowlist rows, read from the join table: the related manager goes through
    ``Experiment.objects``, which hides archived chatbots."""
    return Team.exportable_experiments.through.objects.filter(team=team)


def selected_experiment_ids(team: Team) -> list[int]:
    """The team's allowlist, as stored: working versions only, archived ones included. Empty means
    the whole team."""
    return list(_allowlist(team).values_list("experiment_id", flat=True))


def selected_chatbots(team: Team) -> QuerySet[Experiment]:
    """The allowlisted chatbots themselves, archived ones included."""
    return Experiment._base_manager.filter(pk__in=_allowlist(team).values("experiment_id"))


def family_q(experiment_ids: Sequence[int] | QuerySet) -> Q:
    """Matches the given chatbots and every version of each one."""
    return Q(pk__in=experiment_ids) | Q(working_version_id__in=experiment_ids)


def expand_to_family(experiment_ids: Sequence[int] | QuerySet) -> QuerySet[Experiment]:
    """The selected chatbots plus every version of each one.

    Reads through ``_base_manager`` so archived versions are included: a published copy whose working
    version is missing cannot resolve its self-referential ``working_version`` FK on the target.
    """
    return Experiment._base_manager.filter(family_q(experiment_ids))


def selectable_chatbots(team: Team) -> QuerySet[Experiment]:
    """The chatbots the allowlist picker offers, and the queryset that validates a submitted one.

    Working versions only, so a published version's id can't be submitted. Archived chatbots are
    offered only when they are already in the allowlist, so saving the form keeps them selected.
    """
    return Experiment._base_manager.filter(team=team, working_version__isnull=True).filter(
        Q(is_archived=False) | Q(pk__in=_allowlist(team).values("experiment_id"))
    )


def current_selection_key(team: Team) -> str:
    """The key a sync client derives from this team's allowlist, to check it syncs the same selection."""
    return selection_key([str(public_id) for public_id in selected_chatbots(team).values_list("public_id", flat=True)])
