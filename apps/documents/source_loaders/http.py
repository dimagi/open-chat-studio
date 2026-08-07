"""HTTP helpers shared by the document source loaders."""

import httpx


class ResponseTooLarge(ValueError):
    """A response body ran past the caller's size cap."""


def read_capped(response: httpx.Response, max_bytes: int, label: str) -> bytes:
    """Read a streaming response body, refusing anything past ``max_bytes``.

    The body is consumed in chunks with the running total checked as it goes, so a payload
    whose advertised size was wrong or absent is abandoned partway rather than buffered
    whole into the worker's memory. ``label`` names the source in the error.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(f"Response from {label} exceeds {max_bytes} byte cap")
        chunks.append(chunk)
    return b"".join(chunks)
