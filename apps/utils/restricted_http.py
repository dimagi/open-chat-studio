import contextlib
import json as json_module
import logging
import time
from urllib.parse import urlparse

import httpx
import tenacity
from django.conf import settings

from apps.utils.urlvalidate import InvalidURL, validate_user_input_url

logger = logging.getLogger("restricted_http")

BLOCKED_HEADERS = {"host", "transfer-encoding"}

SENSITIVE_LOG_HEADERS = {"authorization", "x-api-key", "cookie"}


# --- Exceptions ---


class HttpError(Exception):
    """Base exception for all restricted HTTP errors."""


class HttpRequestLimitExceeded(HttpError):
    pass


class HttpRequestTooLarge(HttpError):
    pass


class HttpResponseTooLarge(HttpError):
    pass


class HttpConnectionError(HttpError):
    pass


class HttpTimeoutError(HttpError):
    pass


class HttpInvalidURL(HttpError):
    pass


class HttpAuthProviderError(HttpError):
    pass


# --- Retry helpers ---


class _RetryableRequestError(Exception):
    """Wrapper to signal a retryable HTTP status (429, 502, 503, 504)."""

    def __init__(self, response, body=b""):
        self.response = response
        self.body = body
        super().__init__(f"HTTP {response.status_code}")


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, _RetryableRequestError))


class _wait_from_response_header(tenacity.wait.wait_base):
    """Extract wait time from the Retry-After header on retryable responses."""

    def __init__(self, header: str, max_wait: float):
        self.header = header
        self.max_wait = max_wait

    def __call__(self, retry_state: tenacity.RetryCallState) -> float:
        if retry_state.outcome is None or not retry_state.outcome.failed:
            return 0
        exc = retry_state.outcome.exception()
        if isinstance(exc, _RetryableRequestError):
            retry_after = exc.response.headers.get(self.header)
            if retry_after:
                try:
                    return min(max(float(retry_after), 1), self.max_wait)
                except ValueError:
                    pass
        return 0


# --- Main client ---


class RestrictedHttpClient:
    """HTTP client available inside the restricted Python sandbox."""

    # Expose exception classes as attributes so sandbox code can catch them
    # without imports: ``except http.TimeoutError: ...``
    Error = HttpError
    TimeoutError = HttpTimeoutError
    ConnectionError = HttpConnectionError
    InvalidURL = HttpInvalidURL
    RequestLimitExceeded = HttpRequestLimitExceeded
    RequestTooLarge = HttpRequestTooLarge
    ResponseTooLarge = HttpResponseTooLarge
    AuthProviderError = HttpAuthProviderError

    def __init__(self, team=None):
        self._team = team
        self._request_count = 0
        self._auth_cache = {}  # name -> auth headers dict
        self._validated_hosts = set()  # (hostname, port) tuples

    # --- Public API ---

    def get(self, url, *, params=None, headers=None, auth=None, timeout=None):
        return self._request("GET", url, params=params, headers=headers, auth=auth, timeout=timeout)

    def post(self, url, *, params=None, headers=None, auth=None, json=None, data=None, files=None, timeout=None):
        return self._request(
            "POST", url, params=params, headers=headers, auth=auth, json=json, data=data, files=files, timeout=timeout
        )

    def put(self, url, *, params=None, headers=None, auth=None, json=None, data=None, files=None, timeout=None):
        return self._request(
            "PUT", url, params=params, headers=headers, auth=auth, json=json, data=data, files=files, timeout=timeout
        )

    def patch(self, url, *, params=None, headers=None, auth=None, json=None, data=None, files=None, timeout=None):
        return self._request(
            "PATCH", url, params=params, headers=headers, auth=auth, json=json, data=data, files=files, timeout=timeout
        )

    def delete(self, url, *, params=None, headers=None, auth=None, timeout=None):
        return self._request("DELETE", url, params=params, headers=headers, auth=auth, timeout=timeout)

    # --- Internal ---

    def _request(
        self, method, url, *, params=None, headers=None, auth=None, json=None, data=None, files=None, timeout=None
    ):
        if self._request_count >= settings.RESTRICTED_HTTP_MAX_REQUESTS:
            raise HttpRequestLimitExceeded(f"Request limit of {settings.RESTRICTED_HTTP_MAX_REQUESTS} exceeded")

        # Validate URL (SSRF prevention) — cached per (hostname, port)
        self._validate_url(url)

        # Validate mutual exclusivity
        if json is not None and data is not None:
            raise ValueError("'json' and 'data' are mutually exclusive")
        if json is not None and files is not None:
            raise ValueError("'json' and 'files' are mutually exclusive")

        # Validate and filter headers
        request_headers = self._prepare_headers(headers, auth)

        # Validate body sizes
        self._check_body_size(json=json, data=data)

        # Resolve files
        resolved_files, opened_handles = self._resolve_files(files)

        # Clamp timeout
        if timeout is None:
            timeout = settings.RESTRICTED_HTTP_DEFAULT_TIMEOUT
        timeout = min(max(float(timeout), 1), settings.RESTRICTED_HTTP_MAX_TIMEOUT)

        # Build httpx kwargs
        httpx_kwargs = {
            "method": method,
            "url": url,
            "params": params,
            "headers": request_headers,
            "timeout": timeout,
            "follow_redirects": False,
        }

        if json is not None:
            httpx_kwargs["content"] = json_module.dumps(json).encode("utf-8")
            if "content-type" not in {k.lower() for k in request_headers}:
                httpx_kwargs["headers"] = {**request_headers, "Content-Type": "application/json"}
        elif files is not None:
            httpx_kwargs["files"] = resolved_files
            if data is not None:
                httpx_kwargs["data"] = data
        elif data is not None:
            if isinstance(data, dict):
                httpx_kwargs["data"] = data
            else:
                httpx_kwargs["content"] = data if isinstance(data, bytes) else str(data).encode("utf-8")

        try:
            return self._execute_with_retries(httpx_kwargs)
        finally:
            for fh in opened_handles:
                with contextlib.suppress(Exception):
                    fh.close()

    def _validate_url(self, url):
        parsed = urlparse(url)
        host_key = (parsed.hostname, parsed.port)
        if host_key in self._validated_hosts:
            return
        try:
            validate_user_input_url(url, strict=not settings.DEBUG)
        except InvalidURL as exc:
            raise HttpInvalidURL(str(exc)) from exc
        self._validated_hosts.add(host_key)

    def _execute_with_retries(self, httpx_kwargs):
        max_wait = 10

        retry_controller = tenacity.Retrying(
            reraise=True,
            stop=tenacity.stop_after_attempt(3),
            wait=tenacity.wait.wait_combine(
                _wait_from_response_header("Retry-After", max_wait),
                tenacity.wait_exponential_jitter(initial=1, max=max_wait),
            ),
            retry=tenacity.retry_if_exception(_is_retryable),
            before_sleep=self._log_retry,
        )

        try:
            for attempt in retry_controller:
                with attempt:
                    if self._request_count >= settings.RESTRICTED_HTTP_MAX_REQUESTS:
                        raise HttpRequestLimitExceeded(
                            f"Request limit of {settings.RESTRICTED_HTTP_MAX_REQUESTS} exceeded"
                        )
                    self._request_count += 1
                    result = self._do_request(httpx_kwargs)
                    return result
        except _RetryableRequestError as exc:
            # Retries exhausted on a retryable status — return the response as-is
            return self._build_response_dict(exc.response, body=exc.body)
        except httpx.ConnectError as exc:
            raise HttpConnectionError(f"Connection error: {exc}") from exc
        except (httpx.ConnectTimeout, httpx.TimeoutException) as exc:
            raise HttpTimeoutError(f"Request timed out: {exc}") from exc

    def _do_request(self, httpx_kwargs):
        max_response_bytes = settings.RESTRICTED_HTTP_MAX_RESPONSE_BYTES
        method = httpx_kwargs["method"]
        url = httpx_kwargs["url"]

        start = time.monotonic()
        with httpx.Client() as client:
            with client.stream(**httpx_kwargs) as response:
                # Read response body with size limit
                chunks = []
                total_size = 0
                for chunk in response.iter_bytes():
                    total_size += len(chunk)
                    if total_size > max_response_bytes:
                        raise HttpResponseTooLarge(f"Response body exceeds {max_response_bytes} bytes")
                    chunks.append(chunk)

                elapsed_ms = int((time.monotonic() - start) * 1000)
                status_code = response.status_code

                logger.info(
                    "%s %s -> %s (%dms)",
                    method,
                    self._redact_url(url),
                    status_code,
                    elapsed_ms,
                )

                body = b"".join(chunks)

                # Check for retryable status codes
                if status_code in (429, 502, 503, 504):
                    raise _RetryableRequestError(response, body=body)

                return self._build_response_dict(response, body=body)

    def _build_response_dict(self, response, body=b""):
        text = body.decode("utf-8", errors="replace")
        content_type = response.headers.get("content-type", "")

        json_body = None
        if "application/json" in content_type:
            with contextlib.suppress(json_module.JSONDecodeError, ValueError):
                json_body = json_module.loads(text)

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "text": text,
            "json": json_body,
            "is_success": 200 <= response.status_code < 300,
            "is_error": response.status_code >= 400,
        }

    def _prepare_headers(self, user_headers, auth_name):
        headers = {}

        # Resolve auth headers (if any) to know which keys to protect
        auth_headers = {}
        if auth_name is not None:
            auth_headers = self._resolve_auth_headers(auth_name)

        auth_keys_lower = {k.lower() for k in auth_headers}

        # Merge user headers (filtered), skipping blocked and auth-conflicting keys
        if user_headers:
            for key, value in user_headers.items():
                if key.lower() in BLOCKED_HEADERS:
                    continue
                if key.lower() in auth_keys_lower:
                    continue  # auth headers take precedence
                headers[key] = value

        # Apply auth headers last so they always win
        headers.update(auth_headers)

        return headers

    def _resolve_auth_headers(self, auth_name):
        if self._team is None:
            raise HttpAuthProviderError("Auth providers are not available in this context")

        if auth_name in self._auth_cache:
            return self._auth_cache[auth_name]

        from apps.service_providers.models import AuthProvider

        try:
            provider = AuthProvider.objects.get(team=self._team, name__iexact=auth_name)
        except AuthProvider.DoesNotExist:
            available = list(AuthProvider.objects.filter(team=self._team).values_list("name", flat=True))
            raise HttpAuthProviderError(
                f"Auth provider '{auth_name}' not found. Available providers: {', '.join(available) or 'none'}"
            ) from None

        auth_service = provider.get_auth_service()
        auth_headers = auth_service.get_auth_headers()
        self._auth_cache[auth_name] = auth_headers
        return auth_headers

    def _check_body_size(self, json=None, data=None):
        max_request_bytes = settings.RESTRICTED_HTTP_MAX_REQUEST_BYTES

        if json is not None:
            size = len(json_module.dumps(json).encode("utf-8"))
            if size > max_request_bytes:
                raise HttpRequestTooLarge(f"Request body exceeds {max_request_bytes} bytes")

        if data is not None:
            if isinstance(data, bytes):
                size = len(data)
            elif isinstance(data, str):
                size = len(data.encode("utf-8"))
            elif isinstance(data, dict):
                # Form data — estimate size
                size = sum(len(str(k).encode("utf-8")) + len(str(v).encode("utf-8")) for k, v in data.items())
            else:
                size = len(str(data).encode("utf-8"))
            if size > max_request_bytes:
                raise HttpRequestTooLarge(f"Request body exceeds {max_request_bytes} bytes")

    def _resolve_files(self, files):
        """Resolve files parameter into httpx-compatible format.

        Returns (resolved_files, opened_handles) where opened_handles is a list
        of file-like objects that must be closed after the request.
        """
        if files is None:
            return None, []

        max_file_bytes = settings.RESTRICTED_HTTP_MAX_FILE_UPLOAD_BYTES
        opened_handles = []
        total_size = 0

        # Normalize to list of (field_name, value) tuples
        if isinstance(files, dict):
            items = list(files.items())
        elif isinstance(files, list):
            items = files
        else:
            raise ValueError(f"'files' must be a dict or list, got {type(files).__name__}")

        resolved = []
        for field_name, value in items:
            entry, size, handles = self._resolve_file_entry(field_name, value)
            total_size += size
            opened_handles.extend(handles)
            resolved.append((field_name, entry))

        if total_size > max_file_bytes:
            # Close any handles we opened before raising
            for fh in opened_handles:
                with contextlib.suppress(Exception):
                    fh.close()
            raise HttpRequestTooLarge(f"File upload exceeds {max_file_bytes} bytes")

        return resolved, opened_handles

    def _resolve_file_entry(self, field_name, value):
        """Convert a files value into an httpx-compatible tuple.

        Returns (httpx_tuple, size, opened_handles).
        """
        from apps.channels.datamodels import Attachment

        if isinstance(value, Attachment):
            file_obj = value._file
            if not file_obj:
                raise HttpError(f"Attachment '{value.name}' (id={value.file_id}) not found")
            fh = file_obj.file.open("rb")
            return (value.name, fh, value.content_type), value.size, [fh]

        if isinstance(value, tuple):
            if len(value) == 3:
                name, data, content_type = value
                size = len(data) if isinstance(data, bytes) else 0
                return (name, data, content_type), size, []
            raise ValueError(f"File tuple must have 3 elements (name, data, content_type), got {len(value)}")

        if isinstance(value, bytes):
            return (field_name, value, "application/octet-stream"), len(value), []

        raise ValueError(f"Unsupported file value type: {type(value).__name__}")

    def _log_retry(self, retry_state: tenacity.RetryCallState):
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        attempt = retry_state.attempt_number
        if isinstance(exc, _RetryableRequestError):
            logger.info(
                "HTTP %s, retrying (attempt %d/3)",
                exc.response.status_code,
                attempt + 1,
            )
        else:
            logger.info(
                "Connection error: %s, retrying (attempt %d/3)",
                exc,
                attempt + 1,
            )

    @staticmethod
    def _redact_url(url):
        """Remove query string from URL for logging."""
        return url.split("?")[0] if "?" in url else url
