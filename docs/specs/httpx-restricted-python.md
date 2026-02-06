# Spec: HTTP Requests in the Restricted Python Environment

## Background

The `RestrictedPythonExecutionMixin` (`apps/utils/python_execution.py`) provides a sandboxed
Python environment used by `CodeNode` (pipeline nodes) and `PythonEvaluator` (evaluation scripts).
Today the environment allows `json`, `re`, `datetime`, `time`, and `random` but has no network
access. Users who need to call external APIs from a Python node must use the Custom Action
(OpenAPI tool) system, which requires defining an OpenAPI schema upfront.

This spec proposes exposing a restricted `httpx`-based HTTP client inside the sandbox so that
user-authored code can make outbound HTTP requests directly, with guardrails for security,
resource consumption, and reliability.

## Goals

1. Allow user code in `CodeNode` and `PythonEvaluator` to make synchronous HTTP requests
   (GET, POST, PUT, PATCH, DELETE) to external services.
2. Prevent SSRF by reusing the existing URL validation infrastructure.
3. Enforce per-execution limits on request count, response size, and wall-clock time.
4. Provide automatic retries with exponential backoff for transient failures.
5. Surface clear, actionable errors to the user when requests fail.

## Non-Goals

- Async/streaming HTTP (the sandbox executes synchronously).
- WebSocket or long-lived connections.
- Mutual TLS / client certificate auth (can be added later).
- Persistent connection pooling across executions (each execution is isolated).

---

## Design

### 1. Wrapper Module: `RestrictedHttpClient`

Rather than exposing the `httpx` module directly, inject a purpose-built wrapper class into the
sandbox globals. The wrapper encapsulates all safety checks, limiting what user code can control.

```
# apps/utils/restricted_http.py

class RestrictedHttpClient:
    """HTTP client available inside the restricted Python sandbox."""
```

The class is instantiated once per `compile_and_execute_code` call and injected as the global
name `http`. User code interacts with it as:

```python
def main(input, **kwargs):
    response = http.get("https://api.example.com/data", headers={"X-Api-Key": "..."})
    return response["json"]
```

### 2. Public API

The client exposes one method per HTTP verb, all with the same signature:

```python
def get(url, *, params=None, headers=None, timeout=None) -> dict
def post(url, *, params=None, headers=None, json=None, data=None, timeout=None) -> dict
def put(url, *, params=None, headers=None, json=None, data=None, timeout=None) -> dict
def patch(url, *, params=None, headers=None, json=None, data=None, timeout=None) -> dict
def delete(url, *, params=None, headers=None, timeout=None) -> dict
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | `str` | Absolute URL. Must pass SSRF validation. |
| `params` | `dict \| None` | Query string parameters. |
| `headers` | `dict \| None` | Additional request headers. Certain headers are blocked (see Security). |
| `json` | `dict \| list \| None` | JSON-serializable request body. Sets `Content-Type: application/json`. |
| `data` | `str \| bytes \| None` | Raw request body. Mutually exclusive with `json`. |
| `timeout` | `float \| None` | Per-request timeout in seconds. Clamped to `[1, MAX_TIMEOUT]`. Defaults to `DEFAULT_TIMEOUT`. |

**Return value:**

All methods return a plain `dict` (safe for the sandbox) with the following shape:

```python
{
    "status_code": 200,
    "headers": {"content-type": "application/json", ...},  # dict[str, str]
    "text": "...",          # response body as string
    "json": { ... },        # parsed JSON if Content-Type is application/json, else None
    "is_success": True,     # True if 2xx
    "is_error": False,      # True if 4xx or 5xx
}
```

Returning a dict rather than an httpx `Response` object avoids leaking internals into the
sandbox and sidesteps RestrictedPython attribute-access guards.

### 3. Security

#### 3.1 SSRF Prevention

Every URL is validated **before** the request is dispatched by calling
`validate_user_input_url(url, strict=not settings.DEBUG)` from `apps/utils/urlvalidate.py`.
This ensures:

- Only `https://` in production (http allowed in DEBUG).
- Resolved IPs must be globally routable (blocks loopback, private, link-local, reserved,
  multicast ranges).

DNS resolution happens during validation; `httpx` then connects to the already-validated host.
To prevent TOCTOU (time-of-check/time-of-use) attacks where DNS resolves differently between
validation and connection, configure the httpx client's transport to use a custom
`resolve_to_ips`-based resolver that pins the resolution result, or use
`httpx.Client(transport=httpx.HTTPTransport(local_address=...))` patterns. A simpler initial
approach: validate immediately before the request and accept the small TOCTOU window as
acceptable risk for v1, documenting it as a known limitation.

#### 3.2 Blocked Headers

User-supplied headers are filtered to prevent:

- `Host` override (could redirect to internal services at the proxy layer).
- `Transfer-Encoding` manipulation.

Implementation: maintain a `BLOCKED_HEADERS` set, case-insensitively reject any match.

```python
BLOCKED_HEADERS = {"host", "transfer-encoding"}
```

#### 3.3 No Raw Socket Access

The `httpx` module itself is never added to the sandbox globals or the import whitelist.
Users interact exclusively through the `RestrictedHttpClient` wrapper.

#### 3.4 Redirect Policy

Redirects are **not followed automatically**. Each redirect target would need SSRF validation,
and chained redirects complicate reasoning. Users receive the 3xx response and can choose to
issue a follow-up request to the new URL (which will also be validated).

### 4. Resource Limits

All limits are defined as constants in `apps/utils/restricted_http.py` and can be overridden
via Django settings for per-deployment tuning.

| Limit | Default | Setting Key | Rationale |
|-------|---------|-------------|-----------|
| Max requests per execution | 10 | `RESTRICTED_HTTP_MAX_REQUESTS` | Prevents runaway loops. |
| Per-request timeout | 5 seconds | `RESTRICTED_HTTP_DEFAULT_TIMEOUT` | Keeps sandbox execution bounded. |
| Maximum per-request timeout | 30 seconds | `RESTRICTED_HTTP_MAX_TIMEOUT` | User can raise timeout but not unboundedly. |
| Max response body size | 1 MB (1,048,576 bytes) | `RESTRICTED_HTTP_MAX_RESPONSE_BYTES` | Prevents memory exhaustion. |
| Max request body size | 512 KB (524,288 bytes) | `RESTRICTED_HTTP_MAX_REQUEST_BYTES` | Prevents abuse of outbound bandwidth. |

**Enforcement:**

- **Request count:** The client tracks a counter; raises `HttpRequestLimitExceeded` once the
  limit is reached.
- **Timeout:** Passed directly to `httpx.Client(timeout=...)`. The user-supplied timeout is
  clamped: `min(max(user_timeout, 1), MAX_TIMEOUT)`.
- **Response size:** Use `httpx`'s streaming interface. Read the response in chunks, tracking
  cumulative size. If it exceeds the limit, abort the read and raise
  `HttpResponseTooLarge`.
- **Request body size:** Check `len(data)` or `len(json.dumps(json_body))` before sending.
  Raise `HttpRequestTooLarge` if exceeded.

### 5. Retries with Backoff

Transient errors are retried automatically. Users do not need to implement retry logic.

**Retry policy:**

| Parameter | Value |
|-----------|-------|
| Max attempts | 3 (1 initial + 2 retries) |
| Retryable conditions | Connection errors (`httpx.ConnectError`, `httpx.ConnectTimeout`), HTTP 429, HTTP 502/503/504 |
| Backoff strategy | Exponential with jitter: `min(base * 2^attempt + random(0, 1), max_wait)` |
| Base wait | 1 second |
| Max wait | 10 seconds |
| Retry-After header | Honored for 429 responses. Clamped to `[1, max_wait]`. |

**Implementation:** Use `tenacity` (already a project dependency) internally within the
wrapper, consistent with the retry approach in `apps/service_providers/auth_service/main.py`.

```python
retry_controller = tenacity.Retrying(
    reraise=True,
    stop=tenacity.stop_after_attempt(3),
    wait=wait_or(
        wait_from_header("Retry-After"),
        tenacity.wait_exponential_jitter(initial=1, max=10),
    ),
    retry=tenacity.retry_if_exception(is_retryable),
)
```

**Important:** Each retry attempt counts toward the per-execution request limit. If a request
uses all its retries, those count as 3 requests against the limit of 10.

### 6. Error Handling

Errors are translated into clear Python exceptions that surface in the `CodeNodeRunError`
context display. The exception hierarchy:

```
HttpError (base)
├── HttpRequestLimitExceeded    - "Request limit of {n} exceeded"
├── HttpRequestTooLarge         - "Request body exceeds {n} bytes"
├── HttpResponseTooLarge        - "Response body exceeds {n} bytes"
├── HttpConnectionError         - wraps httpx.ConnectError after retries exhausted
├── HttpTimeoutError            - wraps httpx.TimeoutException after retries exhausted
└── HttpInvalidURL              - wraps urlvalidate.InvalidURL
```

Non-2xx responses are **not** raised as exceptions by default. The user checks
`response["is_error"]` or `response["status_code"]` and handles them in code. This follows
the principle of least surprise for users accustomed to `requests`/`httpx` with
`raise_for_status()` being opt-in.

A convenience method `raise_for_status()` is **not** provided since the return type is a dict,
not an object. Users can write:

```python
response = http.get("https://example.com/api")
if response["is_error"]:
    return f"API error: {response['status_code']}"
```

### 7. Integration Points

#### 7.1 `RestrictedPythonExecutionMixin`

Modify `_get_custom_globals()` to accept an optional `http_client` parameter and inject it:

```python
# In RestrictedPythonExecutionMixin._get_custom_globals
custom_globals["http"] = http_client or RestrictedHttpClient()
```

The client is created in `compile_and_execute_code` so each execution gets a fresh instance
with its own request counter.

#### 7.2 `CodeNode`

No changes required beyond what the mixin provides. The `http` global is automatically
available in user code. The existing `_get_custom_functions` mechanism could be used if
node-specific configuration (e.g., per-node request limits) is needed in the future.

#### 7.3 `PythonEvaluator`

HTTP access in evaluators should be **opt-in** and **off by default**. Evaluators run over
potentially large batches and unrestricted HTTP could cause significant external load. Add
a boolean field `allow_http: bool = False` to `PythonEvaluator`. When `False`, the `http`
global is not injected (or is replaced with a stub that raises
`RuntimeError("HTTP requests are not enabled for this evaluator")`).

#### 7.4 Import Guard

`httpx` is **not** added to the allowed import list. The `http` global is the only way to
make requests.

### 8. Logging and Observability

Each request is logged at INFO level with:

- HTTP method and URL (query params redacted)
- Response status code
- Response time in milliseconds
- Whether it was a retry attempt

```
[restricted_http] GET https://api.example.com/data -> 200 (142ms)
[restricted_http] POST https://api.example.com/submit -> 429 (85ms), retrying (attempt 2/3)
```

Sensitive headers (`Authorization`, `X-Api-Key`, `Cookie`) are never logged.

### 9. Testing Strategy

| Layer | What to test |
|-------|-------------|
| Unit: `RestrictedHttpClient` | URL validation rejects private IPs, blocked headers stripped, request count enforced, response size limit enforced, timeout clamping, retry behavior on 429/5xx, retry-after header honored |
| Unit: sandbox integration | `http` global available in CodeNode, not available in PythonEvaluator by default, available when `allow_http=True` |
| Unit: error translation | Each `HttpError` subclass surfaces correctly in `get_code_error_message` output |
| Integration | End-to-end CodeNode execution with mocked external service (use `respx` or `httpx.MockTransport`) |

### 10. Future Considerations

- **Per-team rate limiting:** Use Redis-backed token bucket to enforce org-wide request quotas
  across concurrent pipeline executions. Out of scope for v1.
- **Allowlist/blocklist by domain:** Let teams configure which domains their code nodes can
  reach. Useful for compliance.
- **Response caching:** Cache GET responses within a single pipeline execution to avoid
  redundant calls. Low priority.
- **Async support:** If the sandbox gains async execution support in the future, provide
  `await http.get(...)` variants.
- **TOCTOU hardening:** Pin DNS resolution results and pass them to the httpx transport to
  eliminate the window between validation and connection.

---

## Summary of Changes

| File | Change |
|------|--------|
| `apps/utils/restricted_http.py` | New module: `RestrictedHttpClient`, exception classes, constants |
| `apps/utils/python_execution.py` | Inject `http` global in `_get_custom_globals` / `compile_and_execute_code` |
| `apps/evaluations/evaluators.py` | Add `allow_http` field to `PythonEvaluator` |
| `config/settings.py` | Add `RESTRICTED_HTTP_*` settings with defaults |
| `tests/utils/test_restricted_http.py` | Unit tests for the wrapper |
| `tests/pipelines/test_code_node_http.py` | Integration tests for CodeNode with HTTP |
