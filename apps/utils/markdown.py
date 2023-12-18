import markdown
from django.urls import reverse
from markdown.inlinepatterns import IMAGE_LINK_RE, LINK_RE, ImageInlineProcessor, LinkInlineProcessor


class ResourceLinkInlineProcessor(LinkInlineProcessor):
    def getLink(self, data, index):
        href, title, index, handled = super().getLink(data, index)
        if href.startswith("resource"):
            _, team_slug, resource_id = href.split(":")
            relative_url = reverse("analysis:download_resource", args=[team_slug, resource_id])
            href = relative_url
        return href, title, index, handled


class ResourceImageInlineProcessor(ImageInlineProcessor):
    def getLink(self, data, index):
        href, title, index, handled = super().getLink(data, index)
        if href.startswith("resource"):
            _, team_slug, resource_id = href.split(":")
            relative_url = reverse("analysis:download_resource", args=[team_slug, resource_id])
            href = relative_url
        return href, title, index, handled


class ResourceExtension(markdown.Extension):
    def extendMarkdown(self, md, *args, **kwargs):
        md.inlinePatterns.register(ResourceLinkInlineProcessor(LINK_RE, md), "link", 160)
        md.inlinePatterns.register(ResourceImageInlineProcessor(IMAGE_LINK_RE, md), "image_link", 150)
