# GitHub Issue: Add httpx-based HTTP client to restricted Python environment

> Use this content to create a GitHub issue. Title and body are separated below.

## Title

Add httpx-based HTTP client to restricted Python environment

## Body

### Summary

Allow user-authored code in `CodeNode` (pipeline Python nodes) and `PythonEvaluator` to make outbound HTTP requests via a sandboxed `httpx`-based wrapper, with guardrails for security, resource consumption, reliability, and integration with team-configured `AuthProvider` credentials.

**Spec:** [`docs/specs/httpx-restricted-python.md`](https://github.com/dimagi/open-chat-studio/blob/claude/add-httpx-restricted-python-4QXvk/docs/specs/httpx-restricted-python.md)

---

### Motivation

Today the restricted Python sandbox (`RestrictedPythonExecutionMixin`) has no network access. Users who need to call external APIs from a Python node must use the Custom Action (OpenAPI tool) system, which requires defining a full OpenAPI schema upfront. A lightweight `http.get(url)` / `http.post(url, json=...)` interface inside the sandbox would unblock many use cases without that ceremony.

Critically, users should not need to hard-code API keys or tokens in their Python code. The `auth` parameter lets them reference team-managed `AuthProvider` credentials by name.

---

### Implementation Plan

#### Phase 1: Core `RestrictedHttpClient` wrapper
- [ ] Create `apps/utils/restricted_http.py` with the `RestrictedHttpClient` class
- [ ] Implement HTTP verb methods (`get`, `post`, `put`, `patch`, `delete`) returning a plain `dict`:
  ```python
  {"status_code": 200, "headers": {...}, "text": "...", "json": {...}, "is_success": True, "is_error": False}
  ```
- [ ] Define exception hierarchy: `HttpError` → `HttpRequestLimitExceeded`, `HttpRequestTooLarge`, `HttpResponseTooLarge`, `HttpConnectionError`, `HttpTimeoutError`, `HttpInvalidURL`, `HttpAuthProviderError`

#### Phase 2: AuthProvider integration
- [ ] Accept `auth` parameter (string name) on all HTTP verb methods
- [ ] Look up `AuthProvider` by name within the team scope (`AuthProvider.objects.get(team=team, name=auth_name)`)
- [ ] Call `provider.get_auth_service().get_auth_headers()` to extract auth headers and merge into the request
- [ ] Cache resolved `AuthService` instances per provider name for the lifetime of the execution (avoids repeated DB lookups)
- [ ] On unknown name: raise `HttpAuthProviderError("Auth provider 'X' not found. Available providers: A, B, C")`
- [ ] When `team` is `None` (e.g., `PythonEvaluator` without team context): raise `HttpAuthProviderError("Auth providers are not available in this context")`
- [ ] Credentials never enter sandbox scope — lookup, decryption, and header injection all happen inside the wrapper

#### Phase 3: Security controls
- [ ] SSRF prevention via `validate_user_input_url()` before every request (blocks private/loopback IPs, enforces HTTPS in prod)
- [ ] Block dangerous headers (`Host`, `Transfer-Encoding`) with case-insensitive filter
- [ ] Disable redirect following (`follow_redirects=False`)
- [ ] Do **not** add `httpx` to sandbox import whitelist; the `http` global is the only entry point

#### Phase 4: Resource limits
- [ ] Add settings to `config/settings.py`:

  | Setting | Default |
  |---------|---------|
  | `RESTRICTED_HTTP_MAX_REQUESTS` | 10 per execution |
  | `RESTRICTED_HTTP_DEFAULT_TIMEOUT` | 5s |
  | `RESTRICTED_HTTP_MAX_TIMEOUT` | 30s |
  | `RESTRICTED_HTTP_MAX_RESPONSE_BYTES` | 1 MB |
  | `RESTRICTED_HTTP_MAX_REQUEST_BYTES` | 512 KB |

- [ ] Per-execution request counter, timeout clamping, streamed response size check, request body size check

#### Phase 5: Retries with backoff
- [ ] `tenacity`-based retry (consistent with `AuthService`): 3 attempts, exponential backoff with jitter (1s base, 10s max)
- [ ] Retry on: connection errors, HTTP 429, HTTP 502/503/504
- [ ] Honor `Retry-After` header (reuse `wait_from_header` / `wait_or` from auth service)
- [ ] Count each retry attempt against the per-execution request limit

#### Phase 6: Integration
- [ ] `CodeNode._get_custom_functions`: create `RestrictedHttpClient(team=session.team)` and inject as `http` global (follows `ParticipantDataProxy` pattern of deriving context from `experiment_session`)
- [ ] `PythonEvaluator`: add `allow_http: bool = False`; only inject `http` when enabled; thread team reference through evaluation runner

#### Phase 7: Logging & observability
- [ ] INFO-level logging: method, URL (params redacted), status, duration, retry info
- [ ] Never log sensitive headers (`Authorization`, `X-Api-Key`, `Cookie`)

#### Phase 8: Tests
- [ ] **`RestrictedHttpClient` unit tests:** SSRF rejection, header blocking, limits, retries, timeout clamping
- [ ] **Auth provider tests:** lookup by name resolves correct provider, auth headers injected, unknown name raises `HttpAuthProviderError` with available names listed, cross-team lookup blocked, cache hit avoids repeat DB queries, `auth=None` sends no auth headers
- [ ] **Sandbox integration tests:** `http` available in CodeNode, opt-in for PythonEvaluator
- [ ] **Error translation tests:** each `HttpError` subclass surfaces correctly in `get_code_error_message`
- [ ] **End-to-end tests:** CodeNode execution with mocked HTTP (via `respx` or `httpx.MockTransport`) including auth provider round-trip

---

### User-facing API example

```python
def main(input, **kwargs):
    # Anonymous request
    resp = http.get("https://api.example.com/public")

    # Authenticated request using a team-configured auth provider
    resp = http.post(
        "https://api.example.com/data",
        auth="My API Key",
        json={"query": input},
    )

    if resp["is_error"]:
        return f"Error: {resp['status_code']}"
    return resp["json"]["result"]
```

### Future considerations (out of scope)

- Per-team rate limiting via Redis token bucket
- Domain allowlist/blocklist per team
- Response caching for GETs within a single pipeline execution
- TOCTOU hardening by pinning DNS resolution into httpx transport
