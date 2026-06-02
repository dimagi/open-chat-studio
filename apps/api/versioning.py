import re

from rest_framework.exceptions import NotFound
from rest_framework.versioning import BaseVersioning


class URLPathVersioning(BaseVersioning):
    """Determine the API version from the URL path (``/api/<version>/...``).

    Unlike DRF's stock :class:`~rest_framework.versioning.URLPathVersioning`, the version is read
    from the request path rather than a captured URL kwarg. This means:

    * the version segment is never threaded through view signatures (many of our function-based
      views take no ``version`` argument), and
    * ``reverse()`` produces the canonical *unversioned* URLs — the permanent ``/api/...`` alias of
      v1 — so existing callers and hyperlinked serializer fields keep working unchanged.

    A request without a version segment (the unversioned alias) resolves to ``default_version``.
    """

    invalid_version_message = "Invalid API version."
    _version_pattern = re.compile(r"^/api/(?P<version>v\d+)/")

    def determine_version(self, request, *args, **kwargs):
        match = self._version_pattern.match(request.path_info)
        version = match.group("version") if match else self.default_version
        if not self.is_allowed_version(version):
            raise NotFound(self.invalid_version_message)
        return version
