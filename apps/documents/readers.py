import logging
from io import BytesIO

from bs4 import UnicodeDammit
from markitdown import MarkItDown
from markitdown._exceptions import UnsupportedFormatException
from pydantic import BaseModel, Field

from apps.files.models import File

logger = logging.getLogger("ocs.documents")


class FileReadException(Exception):
    pass


class DocumentPart(BaseModel):
    content: str
    metadata: dict = Field(default_factory=dict)


class Document(BaseModel):
    parts: list[DocumentPart] = Field(default_factory=list)
    """List of parts of the document. Could be pages or chunks of text."""
    metadata: dict = Field(default_factory=dict)
    """Arbitrary metadata associated with the document."""

    @classmethod
    def from_file(cls, file: File):
        reader = get_file_content_reader(file.content_type)
        with file.file.open("rb") as fh:
            return reader(fh).with_metadata(
                {
                    "source_file_id": file.id,
                    "source_file_name": file.name,
                    "source_content_type": file.content_type,
                }
            )

    def with_metadata(self, metadata: dict):
        return Document(parts=self.parts, metadata={**self.metadata, **metadata})

    def get_contents_as_string(self):
        return "".join(part.content for part in self.parts)


def get_file_content_reader(content_type) -> callable:
    if content_type in READERS:
        return READERS[content_type]
    mime_class = content_type.split("/")[0]
    if mime_class in READERS:
        return READERS[mime_class]

    logger.warning(f"No reader found for content type {content_type}. Using default text reader.")
    return markitdown_read


def markitdown_read(file_obj) -> Document:
    # markitdown supports text, pdf, docx, xlsx, xls, outlook, pptx which will be handled by the default text reader
    md = MarkItDown(enable_plugins=False)
    try:
        result = md.convert(BytesIO(file_obj.read()))
        return Document(parts=[DocumentPart(content=result.markdown)])
    except UnsupportedFormatException:
        return plaintext_reader(file_obj)
    except UnicodeDecodeError as e:
        raise FileReadException("Unable to decode file contents to text") from e


def plaintext_reader(file_obj) -> Document:
    content = file_obj.read()
    try:
        # UTF-8 decode
        content = content.decode()
    except UnicodeDecodeError:
        # Try to detect encoding
        try:
            detected_content = UnicodeDammit(content).unicode_markup
            if detected_content is None:
                raise FileReadException("Unable to detect file encoding")
            content = detected_content
        except FileReadException:
            raise
        except Exception as e:
            raise FileReadException("Unable to decode file contents to text") from e
    return Document(parts=[DocumentPart(content=content)])


READERS = {None: markitdown_read, "text/markdown": plaintext_reader, "text/plain": plaintext_reader}
