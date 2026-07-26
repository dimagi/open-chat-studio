from django.urls import path, re_path

from apps.api.export import views
from apps.teams.export.manifest import MANIFEST_ENTRIES

app_name = "export"

# The team-export surface, mounted at OCS's API root under ``/api/export/`` (a standalone surface,
# not part of the versioned v1/v2 API). The team itself is served as a single object at ``team/``,
# the manifest (call order + per-model config) at ``manifest/``, and each synced resource at its own
# literal ``<resource>/`` path so OpenAPI can document a distinct response schema per resource. A
# catch-all 404s any other ``<x>/`` so unknown resources get a JSON 404, not Django's HTML one.
# Literal paths precede the catch-all so known resources match first.
urlpatterns = [
    path("team/", views.TeamView.as_view(), name="team"),
    path("manifest/", views.ManifestView.as_view(), name="manifest"),
    *[
        path(
            f"{entry.resource}/",
            views.resource_view(entry).as_view(),
            {"resource": entry.resource},
            name=f"resource-{entry.resource}",
        )
        for entry in MANIFEST_ENTRIES
    ],
    re_path(r"^(?P<resource>[^/]+)/$", views.UnknownResourceView.as_view(), name="resource"),
]
