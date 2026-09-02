from copy import copy

import markdown
from django.urls import reverse
from markdown.inlinepatterns import (
    IMAGE_LINK_RE,
    IMAGE_REFERENCE_RE,
    LINK_RE,
    REFERENCE_RE,
    ImageInlineProcessor,
    ImageReferenceInlineProcessor,
    LinkInlineProcessor,
    ReferenceInlineProcessor,
    ShortImageReferenceInlineProcessor,
    ShortReferenceInlineProcessor,
)

# Prefix used by the OpenAI assistants file downloads that this codebase no longer serves.
LEGACY_ASSISTANT_FILE_PREFIX = "assistant_file"


class LinkProcessorMixin:
    def handleMatch(self, m, data):
        el, start, end = super().handleMatch(m, data)
        return _update_href(el), start, end


class OcsLinkInlineProcessor(LinkProcessorMixin, LinkInlineProcessor):  # ty: ignore[invalid-method-override]
    pass


class OcsReferenceInlineProcessor(LinkProcessorMixin, ReferenceInlineProcessor):  # ty: ignore[invalid-method-override]
    pass


class OcsShortReferenceInlineProcessor(LinkProcessorMixin, ShortReferenceInlineProcessor):  # ty: ignore[invalid-method-override]
    pass


class FileShortReferenceInlineProcessor(LinkProcessorMixin, ShortReferenceInlineProcessor):  # ty: ignore[invalid-method-override]
    pass


class OcsImageInlineProcessor(LinkProcessorMixin, ImageInlineProcessor):  # ty: ignore[invalid-method-override]
    pass


class OcsImageReferenceInlineProcessor(LinkProcessorMixin, ImageReferenceInlineProcessor):  # ty: ignore[invalid-method-override]
    pass


class OcsShortImageReferenceInlineProcessor(LinkProcessorMixin, ShortImageReferenceInlineProcessor):  # ty: ignore[invalid-method-override]
    pass


def _update_href(el):
    """Rewrite `file:` hrefs to their download URL, returning the updated element.

    Legacy `assistant_file:` hrefs are returned as their plain link/alt text instead: the
    assistants UI and its download view were removed (#4254), so there is nothing left to
    link to and the raw href would otherwise survive sanitisation as a dead-scheme link.
    """
    if el is None:
        return el

    el = copy(el)
    attr = _url_attr(el)
    if attr is None:
        return el
    if el.tag == "a":
        el.set("target", "_blank")

    href = el.get(attr) or ""
    if href.startswith(f"{LEGACY_ASSISTANT_FILE_PREFIX}:"):
        return _link_text(el)

    download_url = _file_download_url(href)
    if download_url:
        el.set(attr, download_url)
    return el


def _url_attr(el):
    """Return the attribute holding `el`'s URL, or None if it isn't a link or an image."""
    return {"a": "href", "img": "src"}.get(el.tag)


def _link_text(el):
    """The text a link or image shows once it is stripped of its href."""
    return el.text or el.get("alt") or ""


def _file_download_url(href):
    """Return the download URL for a `file:team:owner:file` href, or None for anything else."""
    parts = href.split(":")
    if len(parts) != 4 or parts[0] != "file":
        return None
    _, team_slug, owner_id, file_id = parts
    return reverse("experiments:download_file", args=[team_slug, owner_id, file_id])


class FileExtension(markdown.Extension):
    def extendMarkdown(self, md, *args, **kwargs):
        md.inlinePatterns.register(OcsReferenceInlineProcessor(REFERENCE_RE, md), "reference", 170)
        md.inlinePatterns.register(OcsLinkInlineProcessor(LINK_RE, md), "link", 160)
        md.inlinePatterns.register(OcsImageInlineProcessor(IMAGE_LINK_RE, md), "image_link", 150)
        md.inlinePatterns.register(OcsImageReferenceInlineProcessor(IMAGE_REFERENCE_RE, md), "image_reference", 140)
        md.inlinePatterns.register(
            OcsShortImageReferenceInlineProcessor(IMAGE_REFERENCE_RE, md), "short_image_ref", 125
        )
        md.inlinePatterns.register(OcsShortReferenceInlineProcessor(REFERENCE_RE, md), "short_reference", 130)
