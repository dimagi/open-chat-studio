import logging
import threading
from io import BytesIO

from bs4 import UnicodeDammit
from pydantic import BaseModel, Field

from apps.files.models import File

logger = logging.getLogger("ocs.documents")

PDF_MAGIC = b"%PDF-"
PDF_HEADER_SCAN_BYTES = 1024

_pdfium_lock = threading.Lock()


def _looks_like_pdf(head: bytes) -> bool:
    """The PDF header may sit anywhere in the first 1024 bytes, not just at offset zero;
    mail gateways and misbehaving HTTP servers produce such files, and PDFium reads them."""
    return PDF_MAGIC in head[:PDF_HEADER_SCAN_BYTES]


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
        with file.file.open("rb") as fh:
            return read_file_content(fh, file.content_type).with_metadata(
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


def read_file_content(file_obj, content_type: str | None = None) -> "Document":
    """Read a file to text, picking a reader by content type.

    The single funnel both the document sync and index ingestion use, so a file that cannot
    be read raises ``FileReadException`` for either and is recorded as failed.
    """
    reader = get_file_content_reader(content_type)
    return reader(file_obj)


def get_file_content_reader(content_type) -> callable:
    if content_type in READERS:
        return READERS[content_type]
    mime_class = content_type.split("/")[0]
    if mime_class in READERS:
        return READERS[mime_class]

    logger.warning(f"No reader found for content type {content_type}. Sniffing the content instead.")
    return default_read


def _pdf_page_text(page) -> str:
    """Text of one PDFium page, normalised and newline-terminated.

    ``Document.get_contents_as_string`` concatenates parts with no separator, so each page
    carries its own break or the last word of one page runs into the first word of the next.
    PDFium also emits \\r\\n, which would otherwise reach the index verbatim.
    """
    text = page.get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def pdf_read(file_obj) -> Document:
    """Read a PDF with PDFium, one part per page.

    PDFium is ~115x faster than pdfminer on a large document, and its text is better:
    pdfminer's layout analysis fragments complex pages into single characters (65%
    one-character tokens on a table-heavy 144-page report, against 8% for PDFium) and
    scatters table columns away from the rows they belong to.

    There is deliberately no pdfminer fallback for a file PDFium cannot open. PDFium already
    recovers the damage worth recovering -- it rebuilds a clobbered xref table in under a
    millisecond, the same damage that took pdfminer ~13 minutes to return a fragment of --
    and pdfminer's slow path cannot be bounded once it starts: it is CPU-bound pure Python,
    so a thread cannot be cancelled, and a SIGALRM budget fires only on the main thread,
    which is not where our gevent or threaded workers run requests.

    PDFium itself is not thread-safe, and both the Celery workers (--pool threads) and
    gunicorn (--threads) parse PDFs concurrently in one process, so a module lock serialises
    every parse: unsynchronised concurrent use corrupts library state and fails valid PDFs.
    """
    import pypdfium2  # noqa: PLC0415 - TID253: loads a shared library, keep off the import path

    file_obj.seek(0)
    data = file_obj.read()
    try:
        with _pdfium_lock:
            pdf = pypdfium2.PdfDocument(data)
            try:
                parts = [DocumentPart(content=_pdf_page_text(page)) for page in pdf]
            finally:
                pdf.close()
    except Exception as exc:
        raise FileReadException(f"Could not extract text from this PDF: {exc}") from exc
    return Document(parts=parts)


def default_read(file_obj) -> Document:
    """Read a file whose content type we have no trustworthy claim about.

    The sync loader takes file types from a third-party feed, so the bytes decide: a
    mislabelled PDF would otherwise reach pdfminer and cost minutes instead of milliseconds.
    """
    file_obj.seek(0)
    head = file_obj.read(PDF_HEADER_SCAN_BYTES)
    file_obj.seek(0)
    if _looks_like_pdf(head):
        return pdf_read(file_obj)
    return markitdown_read(file_obj)


def markitdown_read(file_obj) -> Document:
    # markitdown supports text, pdf, docx, xlsx, xls, outlook, pptx which will be handled by the default text reader
    from markitdown import MarkItDown  # noqa: PLC0415 - TID253: heavy lib, slow startup
    from markitdown._exceptions import (  # noqa: PLC0415 - TID253: heavy lib, slow startup
        FileConversionException,
        UnsupportedFormatException,
    )

    md = MarkItDown(enable_plugins=False)
    content = file_obj.read()
    try:
        result = md.convert(BytesIO(content))
        return Document(parts=[DocumentPart(content=result.markdown)])
    except (FileConversionException, UnsupportedFormatException) as exc:
        # Falling back to a plaintext decode is useful for a genuinely textual file with an
        # unrecognised extension. For a PDF it is worse than failing: decoding the raw bytes
        # yields megabytes of noise (mean word length ~158 characters) that would be indexed
        # and retrieved as if it were the document.
        if _looks_like_pdf(content):
            raise FileReadException("Could not extract text from this PDF") from exc
        return plaintext_reader(BytesIO(content))
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
        except Exception as e:
            raise FileReadException("Unable to decode file contents to text") from e
    return Document(parts=[DocumentPart(content=content)])


READERS = {
    None: default_read,
    "application/pdf": pdf_read,
    "application/x-pdf": pdf_read,
    "text/markdown": plaintext_reader,
    "text/plain": plaintext_reader,
}
