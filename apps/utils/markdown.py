import markdown
from django.urls import reverse
from markdown.inlinepatterns import IMAGE_LINK_RE, LINK_RE, ImageInlineProcessor, LinkInlineProcessor


class FileLinkInlineProcessor(LinkInlineProcessor):
    def getLink(self, data, index):
        href, title, index, handled = super().getLink(data, index)
        if href.startswith("file:"):
            _, team_slug, session_id, file_id = href.split(":")
            relative_url = reverse("experiments:download_file", args=[team_slug, session_id, file_id])
            href = relative_url
        return href, title, index, handled


class FileImageInlineProcessor(ImageInlineProcessor):
    def getLink(self, data, index):
        href, title, index, handled = super().getLink(data, index)
        if href.startswith("file:"):
            _, team_slug, session_id, file_id = href.split(":")
            relative_url = reverse("experiments:download_file", args=[team_slug, session_id, file_id])
            href = relative_url
        return href, title, index, handled


class FileExtension(markdown.Extension):
    def extendMarkdown(self, md, *args, **kwargs):
        md.inlinePatterns.register(FileLinkInlineProcessor(LINK_RE, md), "link", 160)
        md.inlinePatterns.register(FileImageInlineProcessor(IMAGE_LINK_RE, md), "image_link", 150)
