from django.urls import reverse
from django.utils.translation import gettext as _

from apps.generics.breadcrumbs import Crumb
from apps.human_annotations.models import AnnotationQueue


def queues_crumbs(team_slug: str, queue: AnnotationQueue | None = None) -> list[Crumb]:
    crumbs: list[Crumb] = [(_("Annotations"), reverse("human_annotations:queue_home", args=[team_slug]))]
    if queue:
        crumbs.append((queue.name, queue.get_absolute_url()))
    return crumbs
