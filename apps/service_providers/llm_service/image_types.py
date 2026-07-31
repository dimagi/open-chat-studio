"""Image formats each LLM provider accepts as multimodal input.

Leaf module (no app imports) so llm_service.main, llm_service.utils, and the
chat upload API can all use it without cycles.
"""

DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
# Gemini accepts HEIC/HEIF but not GIF.
GEMINI_SUPPORTED_IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"})
ANY_PROVIDER_SUPPORTED_IMAGE_CONTENT_TYPES = (
    DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES | GEMINI_SUPPORTED_IMAGE_CONTENT_TYPES
)

# Image extensions no provider accepts: rejected at upload regardless of the
# claimed content type, because sniffing is best-effort (a leading comment is
# enough to stop libmagic identifying an SVG).
DENIED_IMAGE_EXTENSIONS = frozenset({".svg", ".svgz", ".bmp", ".tif", ".tiff"})


def image_type_names(content_types: frozenset[str]) -> str:
    return ", ".join(sorted(t.removeprefix("image/").upper() for t in content_types))
