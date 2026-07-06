import re

from rest_framework.exceptions import NotFound
from rest_framework.versioning import BaseVersioning
from rest_framework.versioning import URLPathVersioning as DRFURLPathVersioning


class URLPathVersioning(DRFURLPathVersioning):
    """Determine the API version from the URL path (``/api/<version>/...``).

    Unlike DRF's stock :class:`~rest_framework.versioning.URLPathVersioning`, the version is read
    from the request path rather than a captured URL kwarg. This means:

    * the version segment is never threaded through view signatures (many of our function-based
      views take no ``version`` argument), and
    * ``reverse()`` produces the canonical *unversioned* URLs — the permanent ``/api/...`` alias of
      v1 — so existing callers and hyperlinked serializer fields keep working unchanged.

    A request without a version segment (the unversioned alias) resolves to ``default_version``.

    We subclass DRF's ``URLPathVersioning`` (rather than ``BaseVersioning``) so drf-spectacular
    recognises us as a supported versioning class and emits a per-version schema for each
    ``--api-version`` (without it, every path leaks into one combined schema).
    """

    invalid_version_message = "Invalid API version."
    # drf-spectacular substitutes ``version_param`` into the path when emitting per-version schemas.
    # The stock default is ``"version"``, which would collide with the ``{version}`` path parameter
    # the openai/channels endpoints use for the *experiment* version. Name it so it matches no path.
    version_param = "api_version"
    _version_pattern = re.compile(r"^/api/(?P<version>v\d+)/")

    def determine_version(self, request, *args, **kwargs):
        match = self._version_pattern.match(request.path_info)
        version = match.group("version") if match else self.default_version
        if not self.is_allowed_version(version):
            raise NotFound(self.invalid_version_message)
        return version

    def reverse(self, *args, **kwargs):
        # Skip DRF's version-kwarg injection: our version lives in the path text, not a kwarg, so
        # always emit the canonical unversioned URL (BaseVersioning's behaviour).
        return BaseVersioning.reverse(self, *args, **kwargs)


class ExportVersioning(URLPathVersioning):
    """Versioning for the standalone export surface at ``/api/export/`` (the team data sync).

    The export API isn't part of the versioned v1/v2 surface -- it sits at its own root-level path.
    But drf-spectacular separates schemas by version, so to give export its own schema (and keep it
    out of the v1/v2 schemas) we report a constant ``"export"`` pseudo-version. The export schema
    view is served with ``api_version="export"``, so only these views land in it. At runtime the
    value is harmless: the export views never read ``request.version``.
    """

    EXPORT_VERSION = "export"

    def determine_version(self, request, *args, **kwargs):
        return self.EXPORT_VERSION
