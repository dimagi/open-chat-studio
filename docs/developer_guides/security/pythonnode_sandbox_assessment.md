# PythonNode Sandbox — Security Assessment

**Date:** 2026-08-20
**Component:** `CodeNode` (Python Node) — `apps/pipelines/nodes/nodes.py`
**Sandbox engine:** `RestrictedPythonExecutionMixin` — `apps/utils/python_execution.py`
**RestrictedPython version:** 8.1
**Method:** The sandbox's globals/builtins construction was replicated faithfully and
probed with ~60 escape, resource-exhaustion, and state-tampering test cases. Findings
below were confirmed by execution against RestrictedPython 8.1; the runtime wiring was
verified by reading the production code paths.

> **Scope note.** This is a defensive assessment of our own sandbox. It documents
> where the isolation boundary holds and where it leaks, with reproductions and
> remediations. No fixes are applied in this document.

---

## Executive summary

The RestrictedPython layer is doing its core job well: arbitrary code execution,
filesystem access, arbitrary imports, `eval`/`exec`, and the classic
`__class__ → __subclasses__` breakout chains are all **blocked** (mostly at compile
time). We found **no way to execute arbitrary native code or read the filesystem** from
inside the node.

However, the sandbox is **language-level only** — there is no process, container, or
syscall isolation around it, and no CPU/memory/wall-clock budget. That leaves three
categories of real weakness:

| # | Finding | Class | Severity | Confirmed |
|---|---------|-------|----------|-----------|
| 1 | Shared module singletons (`json`, `re`, `datetime`, `time`, `random`, `math`, `string`) are mutable and poisonable across executions | Cross-tenant integrity / info-leak | **High** | ✅ |
| 2 | No CPU / memory / wall-clock limit on node execution | Denial of service | **High** | ✅ |
| 3 | SSRF URL validation is not IP-pinned → DNS-rebinding window | SSRF | **Medium** | ✅ (by inspection) |
| 4 | Reserved-session-key protection is a source-regex, trivially bypassed and not enforced at runtime | Integrity (weak control) | **Low–Medium** | ✅ |
| 5 | `_write_` guard is a no-op (`lambda x: x`) — the root cause enabling #1 | Hardening gap | (root cause of #1) | ✅ |
| 6 | Usability bugs: augmented assignment (`+=`) and `class` definitions are broken | Correctness / UX | Low | ✅ |

The single most important finding is **#1**: one malicious or buggy Python node in *any*
tenant's pipeline can silently corrupt shared Python modules for *every other* pipeline
execution running on the same Celery worker process, until that worker restarts.

---

## What the sandbox blocks (the good news)

All of the following were **blocked** (compile-time `SyntaxError` unless noted):

- `import os` / any module outside `{json, re, datetime, time, random}` → `ImportError` at runtime via `guarded_import`.
- `__import__(...)`, and any name/attribute starting with `_` → compile error ("invalid ... because it starts with `_`"). This kills the whole dunder-walk family: `().__class__.__bases__[0].__subclasses__()`, `main.__globals__`, `x.__init__.__globals__`, `json.__loader__`, etc.
- `eval(...)`, `exec(...)`, `compile(...)`, `open(...)`, `globals()`, `vars()`, `getattr(...)`, `type(...)`, `breakpoint(...)` → blocked (compile error or `NameError`).
- `"{0.__class__}".format(x)` → `NotImplementedError` (RestrictedPython blocks `str.format`).
- `class Foo: ...` → blocked (`__metaclass__` not defined). *(This is also a usability bug — see #6.)*
- Attribute reads are additionally guarded at **runtime** by `safer_getattr` (present as `_getattr_` inside the builtins), so dunder access is blocked even when it slips past the compiler.
- `setattr`/`delattr` are the guarded RestrictedPython variants and refuse underscore-prefixed attributes.

The exposed attack surface is a small, curated builtin set (99 names, mostly exception
types and safe scalars/containers), plus five stdlib modules and the injected helper
functions (`http`, `get/set_*_state_key`, participant-data helpers, etc.).

---

## Finding 1 — Shared module singletons are poisonable across executions (High)

### Root cause
`_get_custom_globals()` injects the **actual imported module objects** into every
execution:

```python
# apps/utils/python_execution.py
custom_globals = {
    "__builtins__": cls._get_custom_builtins(),
    "json": json, "re": re, "datetime": datetime, "time": time, "random": random,
    ...
    "_write_": lambda x: x,   # <-- no-op write guard (Finding 5)
}
```

These are process-global singletons. Because `_write_` is a no-op, RestrictedPython's
attribute-write guard does nothing, so `json.dumps = evil` (a non-underscore attribute
write) **succeeds** and mutates the shared module object for the whole worker process.
`utility_builtins` similarly shares `math`, `string`, `random`, `whrandom`.

### Reproduction
Execution A (tenant X's pipeline):
```python
def main(input, **kwargs):
    def evil(*a, **k):
        return "PWNED-BY-TENANT-A"
    json.dumps = evil          # non-underscore attr write, not guarded
    return "patched"
```
Execution B (a *completely separate* pipeline run, fresh globals dict):
```python
def main(input, **kwargs):
    return json.dumps({"safe": 1})
```
**Observed:** Execution B returns `"PWNED-BY-TENANT-A"`. The same holds for
`datetime.MINYEAR = 9999`, `time.sleep = lambda *a: None`, etc.

### Impact
Celery prefork workers are long-lived and process tasks sequentially, so a poisoned
module persists across tasks and **across tenants** on that worker until it restarts.
An attacker can:
- **Break availability/integrity** for every other pipeline on the worker (corrupt
  `json`/`datetime`/`re` output).
- **Exfiltrate data** — a patched `json.dumps`/`json.loads` can capture and stash (e.g.
  via the exposed `http` client on the next invocation, or into shared module state)
  every payload other pipelines serialize, including other tenants' data.

This is not a RestrictedPython bug — it is a wiring choice in our harness (sharing live
singletons + a disabled write guard).

### Remediation
- Set a real write guard: `"_write_": full_write_guard` (from `RestrictedPython.Guards`),
  which returns objects that block attribute/item writes on guarded types.
- Do **not** hand live module singletons to sandboxed code. Expose read-only façades /
  a curated `SimpleNamespace` of the specific functions needed (e.g. `json.dumps`,
  `json.loads`) rather than the module object, so attribute assignment can't reach the
  shared singleton.
- Add a regression test asserting that mutating an exposed module in one execution does
  not affect a subsequent execution.

---

## Finding 2 — No CPU / memory / time budget (High)

### Details
`compile_and_execute_code` calls the user's `main()` directly with no timeout, and there
is **no** `CELERY_TASK_TIME_LIMIT` / `CELERY_TASK_SOFT_TIME_LIMIT` configured
(`config/settings.py`) and no per-node watchdog. RestrictedPython caps `range()` to 1000
elements (`limited_range`) but does not limit loop iterations, string/list
multiplication, or allocations.

### Reproductions (all confirmed)
- **CPU:** `while True: x = x + 1` runs unbounded (killed only by an external timeout).
- **Memory:** `"a" * (50 * 1024 * 1024)` and `[0] * 5_000_000` allocate freely — no
  memory ceiling; scale up for OOM.
- **Blocking:** `time.sleep(...)` is available and blocks the worker; combined with the
  HTTP client (up to 10 requests, each up to a 60s timeout) a node can tie up a worker
  for minutes per message.

### Impact
Any user who can author a pipeline can wedge or OOM a shared Celery worker, degrading
service for all tenants on that worker. `range()` clamping gives a false sense of a
resource limit that loops/allocations bypass entirely.

### Remediation
- Set Celery `task_time_limit` / `task_soft_time_limit` for the pipeline/chat queue as a
  backstop (kills the whole task — coarse but effective).
- Prefer a per-node budget: run node code in a child process with `resource.setrlimit`
  (RLIMIT_CPU, RLIMIT_AS) and a wall-clock kill, or an execution-count/instruction guard.
- Consider removing or capping `time.sleep` for sandboxed code.

---

## Finding 3 — SSRF validation is not IP-pinned (DNS rebinding) (Medium)

### Details
`RestrictedHttpClient._validate_url` calls `validate_user_input_url`, which resolves the
hostname and checks every IP is global/public (`apps/utils/urlvalidate.py`). But the
request itself (`_do_request` → `httpx.Client().stream(...)`) connects **by hostname**
and performs an **independent second DNS resolution**. The validated IPs are never
pinned for the actual connection. Additionally, `_validated_hosts` caches by
`(hostname, port)` and skips validation on subsequent requests to the same host within
an execution.

### Impact
A **DNS-rebinding** attacker (low-TTL record they control) can pass validation with a
public IP, then have httpx resolve the same host to an internal address
(`169.254.169.254` cloud metadata, `127.0.0.1`, RFC1918) at connection time. In
production `strict=not settings.DEBUG` is `True`, so HTTP and literal internal IPs are
blocked and redirects are disabled (`follow_redirects=False`) — good — but the rebinding
window remains. (In `DEBUG`, `strict=False` disables the IP checks entirely — dev only.)

### Remediation
- Resolve the host once, validate the resolved IP, then connect to **that IP** (pin it)
  with the original `Host` header preserved — e.g. a custom httpx transport / resolver,
  or pass the validated IP as the connect target. This closes the TOCTOU between
  validation and connection.
- Re-validate on every request rather than trusting the `(hostname, port)` cache, or key
  the cache on the resolved IP.

---

## Finding 4 — Reserved-session-key control is source-regex only (Low–Medium)

### Details
`CodeNode.check_reserved_session_state_keys` blocks reserved keys
(`user_input`, `outputs`, `attachments`, `remote_context`) by regex-matching the **source
text** for `set_session_state_key("<key>"...`. The **runtime** `set_session_state_key`
(`_set_session_state_key`) performs **no** reserved-key check — it writes the key
unconditionally.

### Reproductions (all bypass the validator)
```python
f = set_session_state_key; f("remote_context", 1)          # alias
k = "remote_" + "context"; set_session_state_key(k, 1)      # computed key
set_session_state_key(*["remote_context", 1])               # star-args
set_session_state_key(**{"key_name": "remote_context", "value": 1})  # kwargs
set_session_state_key("remote_" "context", 1)               # literal concat
```

### Impact
`remote_context` is trusted context injected by the API caller (the page/action the user
is on — `apps/experiments/tasks.py`, `apps/api/serializers.py`) and is intended to be
read-only from within a pipeline. The bypass lets node code forge/overwrite it, so any
downstream node that trusts `remote_context` can be fed attacker-chosen values. Impact is
bounded to the session's own state (the pipeline author is often the same party), which
is why this is Low–Medium rather than High — but the control as written is effectively
security theatre.

### Remediation
Enforce reserved keys at **runtime** inside `_set_session_state_key` (and the
`temp_state` equivalent), raising on a reserved key regardless of how the call is spelled.
Keep the source regex only as an early-feedback nicety, not the security boundary.

---

## Finding 5 — `_write_` guard is a no-op (root cause of #1)

`"_write_": lambda x: x` disables RestrictedPython's write-guard entirely. Effects
observed:
- `obj.public = 777` on any exposed object succeeds (underscore attrs still compile-block).
- Enables the shared-module poisoning in Finding 1.

Fix is covered under Finding 1 (use `full_write_guard`). Called out separately because it
is the underlying mechanism and a one-line change closes the highest-impact vector.

---

## Finding 6 — Usability bugs (Low, correctness)

These break legitimate user code and are worth fixing alongside the security work:

- **Augmented assignment is entirely broken.** `x += 1`, `x -= 1`, `lst += [1]`, etc. all
  raise `NameError: name '_inplacevar_' is not defined`, because the sandbox globals never
  define `_inplacevar_`. Users must write `x = x + 1`. Fix: add
  `"_inplacevar_": protected_inplacevar` (`RestrictedPython.Guards.guarded_inplacevar`,
  or the library's `Guards.protected_inplacevar`) to the globals.
- **`class` definitions fail** (`NameError: __metaclass__`). Nested classes are unusable.
  This happens to close escape vectors, so if classes are intentionally disallowed it
  should be a *clear, documented error*, not a confusing `__metaclass__` NameError.

`while`/`for`, generators (`yield`), decorators, lambdas, comprehensions, walrus (`:=`),
`try/except`, `assert`, `global`, and `del` all work as expected.

---

## Architectural observation

The sandbox is **defense-in-depth of one layer**: RestrictedPython in-process, with no
process/container/seccomp boundary (grep for `nsjail|gvisor|seccomp|subprocess ... python`
finds nothing) and no resource budget. Consequences:

- Any future RestrictedPython bypass (or a mis-wired global like `_write_`) is an
  immediate full-worker compromise with cross-tenant blast radius.
- Resource exhaustion is unmitigated regardless of RestrictedPython correctness.

The highest-leverage hardening is to move node execution into a **budgeted child process**
(rlimits + wall-clock kill) and stop sharing live singletons — that converts the current
"single language-level fence" into real isolation and neutralises Findings 1, 2, and 5 at
once.

## Test coverage gap

There are **no** tests exercising sandbox escapes or resource limits for
`RestrictedPythonExecutionMixin` (only `apps/utils/tests/test_restricted_http.py` covers
the HTTP client). Recommend adding an escape/resource regression suite that encodes the
"blocked" list above plus the Finding-1 cross-execution isolation assertion.
