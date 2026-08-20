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
syscall isolation around it, and no CPU/memory/wall-clock budget. That leaves the
following real weaknesses:

| # | Finding | Class | Severity | Status |
|---|---------|-------|----------|--------|
| 1 | Shared module singletons (`json`, `re`, `datetime`, `time`, `random`, `math`, `string`) are mutable and poisonable across executions | Cross-tenant integrity / info-leak | **High** | **Fixed** (attribute-write vector); follow-up [#4239](https://github.com/dimagi/open-chat-studio/issues/4239) |
| 2 | No CPU / memory / wall-clock limit on node execution | Denial of service | **High** | Open [#4240](https://github.com/dimagi/open-chat-studio/issues/4240) |
| 3 | SSRF URL validation is not IP-pinned → DNS-rebinding window | SSRF | **Medium** | Open [#4241](https://github.com/dimagi/open-chat-studio/issues/4241) |
| 4 | Reserved-session-key protection is a source-regex, trivially bypassed and not enforced at runtime | Integrity (weak control) | **Low–Medium** | **Fixed** [#4242](https://github.com/dimagi/open-chat-studio/issues/4242) |
| 5 | `_write_` guard is a no-op (`lambda x: x`) — the root cause enabling #1 | Hardening gap | (root cause of #1) | **Fixed** |
| 6 | Usability bugs: augmented assignment (`+=`) is broken; `class` definitions fail | Correctness / UX | Low | `+=` **Fixed**; classes tracked [#4243](https://github.com/dimagi/open-chat-studio/issues/4243) |

The single most important finding is **#1**: one malicious or buggy Python node in *any*
tenant's pipeline can silently corrupt shared Python modules for *every other* pipeline
execution running on the same Celery worker process, until that worker restarts.

> **Remediation status.** This PR fixes findings 1 (attribute-write poisoning), 4, 5 and
> the `+=` half of 6 in code, with regression tests in
> `apps/pipelines/tests/test_code_node.py::TestSandboxHardening`. Findings 2 and 3, and
> the residual defense-in-depth work for 1 (not sharing live singletons) and the class
> policy for 6, are tracked in the linked issues because they need infrastructure/design
> decisions. See the per-finding "Remediation" sections for what changed.

---

## What the sandbox blocks (the good news)

All of the following were **blocked** (compile-time `SyntaxError` unless noted):

- `import os` / any module outside `{json, re, datetime, time, random}` → `ImportError` at runtime via `guarded_import`.
- `__import__(...)`, and any name/attribute starting with `_` → compile error ("invalid ... because it starts with `_`"). This kills the whole dunder-walk family: `().__class__.__bases__[0].__subclasses__()`, `main.__globals__`, `x.__init__.__globals__`, `json.__loader__`, etc.
- `eval(...)`, `exec(...)`, `compile(...)`, `open(...)`, `globals()`, `vars()`, `getattr(...)`, `type(...)`, `breakpoint(...)` → blocked (compile error or `NameError`).
- `"{0.__class__}".format(x)` → `NotImplementedError` (RestrictedPython blocks `str.format`).
- `f(*args)` / `f(**kwargs)` call unpacking → `NameError: _apply_ is not defined` (RestrictedPython rewrites these to `_apply_(...)`, which the sandbox does not provide). This is why the `*args`/`**kwargs` spellings in Finding 4 fail at runtime even though they slip past the source regex.
- `class Foo: ...` → fails with `NameError: __metaclass__`. Note this is **missing sandbox setup, not an intentional policy**: RestrictedPython 8.1 rewrites class bodies to reference `__metaclass__` and expects `__name__` in the execution globals, neither of which is provided. It happens to block class-based escape attempts, but should be made an explicit, clearly-messaged policy (see #6 / issue #4243).
- Attribute reads are additionally guarded at **runtime** by `safer_getattr` (present as `_getattr_` inside the builtins), so dunder access is blocked even when it slips past the compiler.
- `setattr`/`delattr` are the guarded RestrictedPython variants (`guarded_setattr`/`guarded_delattr`). These do **not** perform an independent underscore-name check — they route the target through `full_write_guard`, which wraps any non-`dict`/`list` object so the write raises. Underscore-prefixed *names* are instead rejected earlier, at compile time. (Before this PR `_write_` was a no-op, so `full_write_guard` was not actually in effect — see Finding 5.)

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

    json.dumps = evil  # non-underscore attr write, not guarded
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
- **✅ Done in this PR.** `_write_` is now `full_write_guard` (from `RestrictedPython.Guards`).
  It passes plain `dict`/`list` through unchanged — so legitimate item assignment in node
  code (`d["k"] = v`, `xs[0] = v`) keeps working — but wraps every other object (including
  the injected module singletons) so `json.dumps = evil` raises `TypeError`. Verified: a
  poisoning attempt in one execution no longer affects a later execution
  (`TestSandboxHardening::test_shared_module_not_poisoned_across_executions`).
- **Follow-up ([#4239](https://github.com/dimagi/open-chat-studio/issues/4239)):** as
  defense in depth, stop handing live module singletons to sandboxed code — expose curated
  read-only façades of just the functions needed rather than the module object, and give
  each execution its own `random.Random()` so `random.seed()` can't leak RNG state across
  runs. (`full_write_guard` already blocks attribute writes, but shared *mutable* state
  reachable through method calls, like the module-global RNG, is a residual concern.)

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

### Remediation (tracked in [#4240](https://github.com/dimagi/open-chat-studio/issues/4240) — needs a design decision)
- **Per-node budget (preferred):** run node code in a child process with `resource.setrlimit`
  (RLIMIT_CPU, RLIMIT_AS) and a wall-clock kill, or an execution-count/instruction guard.
- **Celery backstop:** set `task_soft_time_limit` / `task_time_limit` on the pipeline/chat
  queue. Mind the semantics: the **soft** limit raises `SoftTimeLimitExceeded` *inside* the
  task, so it must be caught to release resources before deciding to fail or retry; the
  **hard** limit kills and replaces the worker process, so no in-process cleanup runs. If
  `task_acks_late` is in play, any task that can be requeued after worker loss must be
  idempotent. A blanket limit also risks killing legitimately long tasks (LLM calls, evals,
  syncs), so it must be scoped to the right queue/task rather than applied globally.
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

Related: `_do_request` builds `httpx.Client()` without `trust_env=False`, so httpx honors
`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY` from the worker environment by default.
Whether that is desirable (egress via a controlled proxy) or a risk (bypassing the IP
checks entirely by routing through a proxy) depends on deployment and should be decided and
documented explicitly.

### Remediation (tracked in [#4241](https://github.com/dimagi/open-chat-studio/issues/4241))
- Resolve the host once, validate the resolved IP, then connect to **that IP** (pin it) as
  the TCP target while preserving the original `Host` header; for HTTPS also set the TLS
  SNI hostname to the original hostname via httpx request `extensions={"sni_hostname": host}`
  so certificate verification still works. A custom httpx transport / resolver is the clean
  way. This closes the TOCTOU between validation and connection.
- Re-validate on every request rather than trusting the `(hostname, port)` cache, or key the
  cache on the resolved IP that is actually used for the connection.
- Decide and document the `trust_env` / proxy behavior (test direct, proxied, and `NO_PROXY`
  worker environments).

---

## Finding 4 — Reserved-session-key control is source-regex only (Low–Medium)

### Details
`CodeNode.check_reserved_session_state_keys` blocks reserved keys
(`user_input`, `outputs`, `attachments`, `remote_context`) by regex-matching the **source
text** for `set_session_state_key("<key>"...`. The **runtime** `set_session_state_key`
(`_set_session_state_key`) performed **no** reserved-key check before this PR — it wrote
the key unconditionally.

### Reproductions
These forms all evade the source regex. Two are additionally stopped by RestrictedPython
itself (no `_apply_`), so only the first three actually reach the runtime writer:
```python
f = set_session_state_key; f("remote_context", 1)          # alias        — reaches runtime
k = "remote_" + "context"; set_session_state_key(k, 1)      # computed key — reaches runtime
set_session_state_key("remote_" "context", 1)               # literal concat — reaches runtime
set_session_state_key(*["remote_context", 1])               # star-args  — blocked: _apply_ undefined
set_session_state_key(**{"key_name": "remote_context", ...})# kwargs     — blocked: _apply_ undefined
```

### Impact
`remote_context` is trusted context injected by the API caller (the page/action the user
is on — `apps/experiments/tasks.py`, `apps/api/serializers.py`) and is intended to be
read-only from within a pipeline. The bypass lets node code forge/overwrite it, so any
downstream node that trusts `remote_context` can be fed attacker-chosen values. Impact is
bounded to the session's own state (the pipeline author is often the same party), which
is why this is Low–Medium rather than High — but the control as written was effectively
security theatre.

### Remediation
**✅ Done in this PR.** `_set_session_state_key` now raises `CodeNodeRunError` when
`key_name in settings.RESERVED_SESSION_STATE_KEYS`, regardless of how the call is spelled.
The source regex is kept only as early-feedback UX. Covered by
`TestSandboxHardening::test_reserved_session_state_key_enforced_at_runtime` (alias, computed
key, literal concat). Note the `temp_state` setter already enforces its own read-only keys
(`user_input`, `outputs`, `attachments`) at runtime.

---

## Finding 5 — `_write_` guard is a no-op (root cause of #1)

`"_write_": lambda x: x` disabled RestrictedPython's write-guard entirely. Effects
observed (before this PR):
- `obj.public = 777` on any exposed object succeeded (underscore attrs still compile-block).
- Enabled the shared-module poisoning in Finding 1.

**✅ Done in this PR** — `_write_` is now `full_write_guard` (see Finding 1 remediation).
Called out separately because it is the underlying mechanism and a one-line change closes
the highest-impact vector.

---

## Finding 6 — Usability bugs (Low, correctness)

These break legitimate user code and are worth fixing alongside the security work:

- **Augmented assignment was entirely broken.** `x += 1`, `x -= 1`, `lst += [1]`, etc. all
  raised `NameError: name '_inplacevar_' is not defined`, because the sandbox globals never
  defined `_inplacevar_` (RestrictedPython rewrites `x += 1` to `_inplacevar_("+=", x, 1)`).
  **✅ Fixed in this PR:** we register an application-owned `restricted_inplacevar` that
  allowlists the in-place operators and dispatches to `operator.i*`. RestrictedPython 8.1
  ships **no** such helper (there is no `guarded_inplacevar`/`protected_inplacevar` in
  `Guards.py`), so it must be app-owned. Augmented assignment on subscripts/attributes
  (`d["k"] += 1`) stays blocked by RestrictedPython's own compile-time policy — write
  `d["k"] = d["k"] + 1`. Covered by `TestSandboxHardening::test_augmented_assignment_supported`.
- **`class` definitions fail** (`NameError: __metaclass__`). This is missing sandbox setup,
  not an intentional restriction: RestrictedPython 8.1 rewrites class bodies to reference
  `__metaclass__` and expects `__name__` in the execution globals. Decision needed
  ([#4243](https://github.com/dimagi/open-chat-studio/issues/4243)): either support classes
  by supplying a safe `__metaclass__`/`__name__` and retest, or reject class definitions
  explicitly at compile time with a clear message. Left as-is pending that decision.

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

## Test coverage

Before this PR there were **no** tests exercising sandbox escapes for
`RestrictedPythonExecutionMixin` (only `apps/utils/tests/test_restricted_http.py` covered
the HTTP client). This PR adds `TestSandboxHardening` in
`apps/pipelines/tests/test_code_node.py`, covering: module-poisoning is blocked, poisoning
does not leak across executions, augmented assignment works, and reserved keys are enforced
at runtime under the validator-bypass spellings. Recommended follow-up: extend it into a
broader escape/resource regression suite that also encodes the "blocked" list above.
