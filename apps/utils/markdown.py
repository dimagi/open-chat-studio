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


class LinkProcessorMixin:
    def handleMatch(self, m, data):
        el, start, end = super().handleMatch(m, data)
        return _update_href(el), start, end


class OcsLinkInlineProcessor(LinkProcessorMixin, LinkInlineProcessor):
    pass


class OcsReferenceInlineProcessor(LinkProcessorMixin, ReferenceInlineProcessor):
    pass


class OcsShortReferenceInlineProcessor(LinkProcessorMixin, ShortReferenceInlineProcessor):
    pass


class FileShortReferenceInlineProcessor(LinkProcessorMixin, ShortReferenceInlineProcessor):
    pass


class OcsImageInlineProcessor(LinkProcessorMixin, ImageInlineProcessor):
    pass


class OcsImageReferenceInlineProcessor(LinkProcessorMixin, ImageReferenceInlineProcessor):
    pass


class OcsShortImageReferenceInlineProcessor(LinkProcessorMixin, ShortImageReferenceInlineProcessor):
    pass


def _update_href(el):
    if el is None:
        return el

    el = copy(el)
    if el.tag == "img":
        tag = "src"
    elif el.tag == "a":
        tag = "href"
        el.set("target", "_blank")
    else:
        return el

    href = el.get(tag)
    if not href or href.split(":", 1)[0] not in ("file", "assistant_file"):
        return el

    prefix, team_slug, owner_id, file_id = href.split(":")
    # Map file prefixes to URL names
    # Use chatbots for regular files since experiments are migrated to chatbots
    url_name = {
        "file": "chatbots:download_file",
        "assistant_file": "assistants:download_file",
    }.get(prefix)
    if url_name is None:
        return el
    relative_url = reverse(url_name, args=[team_slug, owner_id, file_id])
    el.set(tag, relative_url)
    return el


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
