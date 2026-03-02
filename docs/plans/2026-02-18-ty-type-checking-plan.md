# ty Type Checking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Incrementally adopt ty type checking across the Open Chat Studio codebase, starting with foundation tooling and progressively enabling rules from low-noise to high-noise.

**Architecture:** Install `django-types` for Django stub support, configure ty in `pyproject.toml` with all rules ignored, add non-blocking CI, then enable rules tier-by-tier in separate PRs. Each rule enablement PR fixes all violations for that rule.

**Tech Stack:** ty (Python type checker), django-types (Django stubs), uv (package manager), GitHub Actions CI

**Design doc:** `docs/plans/2026-02-18-ty-type-checking-design.md`

---

## Implementation Status (as of 2026-03-02)

### Completed and merged to main

| Task | Commit(s) | Notes |
|------|-----------|-------|
| Task 1 — Install django-types + ty | (early commits) | Both in `pyproject.toml` dev deps |
| Task 2 — Configure ty, all rules ignored | (early commits) | `[tool.ty]` section in pyproject.toml |
| Task 3 — Non-blocking CI step | `fd2f29598` | `continue-on-error: true` in lint_and_test.yml |
| Task 4 — AGENTS.md ty command | `88b6d464d` | `uv run ty check apps/` documented |
| Task 5 — unresolved-import | `2e942c1dd` | 5 violations → all suppressed |
| Task 6 — Zero-violation rules batch | `16cd31804` | ~30 zero-violation rules enabled as error |
| Task 7 — Remaining low-count Phase 1 rules | `ef68b205f` | invalid-raise, empty-body, etc. |
| Task 8a — unsupported-operator | `37bdc6b3d` | 16 violations fixed |
| Task 8b — invalid-return-type | `c70556231` | 32 violations fixed |
| Task 8c — possibly-missing-attribute | `18de5d9bb` | 44 violations fixed |
| Task 8d — invalid-method-override | `9ff4169f3` | 60 violations suppressed |
| Task 8e — invalid-parameter-default | `6455d549d` | 79 violations fixed (Optional annotations) |
| Task 8f — not-subscriptable | `23e884fa0` | 39 violations suppressed |
| Task 8g — invalid-assignment | `264f8621c` | 108 violations fixed/suppressed |
| Additional cleanup | `17f4c7bb1`, `30d3f3e4b` | 14 + N more suppressions improved |

### In progress — branch `sk/types-IV` (not yet merged)

| Commit | Rule | Initial violations | Suppressions added |
|--------|------|-------------------|-------------------|
| `1b8b42144` | invalid-type-form | 25 | ~3 remaining (25 fixed) |
| `3588e5fc9` | invalid-argument-type | 346 | ~180 remaining (346 addressed) |
| `523b16db7` | unresolved-attribute | 1,642 | enabled as **warn**, not error |
| (uncommitted, this session) | cleanup across all rules | 395 → 352 | 43 suppressions removed |

### Current suppression counts (working tree, 2026-03-02)

Total: **352** `ty: ignore` suppressions

| Rule | Count | Notes |
|------|-------|-------|
| `invalid-argument-type` | 180 | Largest remaining batch — Django/LangChain API mismatches |
| `invalid-assignment` | 99 | Second largest — Django descriptors, lazy objects, etc. |
| `invalid-method-override` | 32 | LSP violations: events models (justified), others |
| `not-subscriptable` | 18 | Third-party types without generics |
| `invalid-return-type` | 10 | channels.py and a few others (callers not guarded for None) |
| `unresolved-import` | 5 | Optional/conditional imports (mailchimp3, etc.) |
| `invalid-type-form` | 3 | Remaining after fixes |
| other | 5 | invalid-key, not-subscriptable+invalid-argument-type combos, etc. |

### Known gotchas discovered during implementation

- **`get_context_data` pattern**: Django URL kwargs flow through `self.kwargs`, not method params. Views that declared `team_slug: str` as an explicit param were LSP violations. Fixed in session by removing the param and reading from `self.kwargs["team_slug"]`.
- **`VersionsMixin.create_new_version` hierarchy**: `**kwargs` on the base alone does not satisfy ty's LSP check — subclasses must explicitly declare `save=True, is_copy=False`. `StaticTrigger`/`TimeoutTrigger` in `events/models.py` are genuine violations (required positional `new_experiment` arg) and kept as justified suppressions.
- **`channels.py` ExperimentSession return type**: Broadening to `ExperimentSession | None` cascades to ~10 call sites that don't guard for None. Reverted; kept ty: ignore.
- **ruff post-Edit hook**: The project runs ruff after each file edit, stripping unused imports. Import + usage changes must be made atomically (use `Write` to rewrite the whole file, or a Python script).
- **`uuid.uuid4` as `CharField` default**: Actual bug — `uuid4()` returns `UUID`, not `str`. Fixed with `lambda: str(uuid.uuid4())` in both model and migration.
- **GET[] vs GET.get()**: Switching from `.get("key")` to `["key"]` changes None-missing behavior to KeyError. Two instances changed in dataset_views.py and evaluation_config_views.py — verify these params are always present.

---

## Task 1: Install django-types and ty as dev dependencies ✅ DONE

**Files:**
- Modify: `pyproject.toml:139-155` (dev dependency group)

**Step 1: Add dependencies**

Add `django-types` and `ty` to the `[dependency-groups] dev` list in `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "ruff",
    "mock",
    "invoke",
    "termcolor",
    "time-machine",
    "watchfiles",
    "pytest",
    "pytest-django",
    "factory-boy",
    "pytest-httpx",
    "pytest-cov",
    "pytest-xdist",
    "django-debug-toolbar>=5.2.0",
    "djlint>=1.36.4",
    "prek>=0.3.2",
    "django-types",
    "ty",
]
```

**Step 2: Lock and sync**

Run:
```bash
uv lock && uv sync --dev
```
Expected: lock file updated, both packages installed.

**Step 3: Verify installation**

Run:
```bash
uv run ty --version
```
Expected: prints ty version (e.g., `ty 0.0.17` or newer).

**Step 4: Re-measure baseline with django-types**

Run:
```bash
uv run ty check apps/ 2>&1 | grep -oP 'rule `[^`]+`' | sort | uniq -c | sort -rn | head -20
uv run ty check apps/ 2>&1 | tail -3
```

Record the new diagnostic counts. The `unresolved-attribute` count should decrease now that django-types provides `.objects` stubs.

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add django-types and ty as dev dependencies"
```

---

## Task 2: Configure ty in pyproject.toml with all rules ignored ✅ DONE

**Files:**
- Modify: `pyproject.toml` (add `[tool.ty]` section after `[tool.djlint]`)

**Step 1: Add ty configuration**

Add this section at the end of `pyproject.toml` (after the `[tool.djlint]` block):

```toml
[tool.ty]
python-version = "3.13"

[tool.ty.src]
exclude = ["migrations/"]

[tool.ty.rules]
all = "ignore"
```

This starts with every rule turned off. We'll enable them one-by-one in later tasks.

**Step 2: Verify ty runs clean**

Run:
```bash
uv run ty check apps/
```
Expected: `All checks passed!` (since all rules are ignored).

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add ty configuration with all rules ignored"
```

---

## Task 3: Add non-blocking ty CI step ✅ DONE

**Files:**
- Modify: `.github/workflows/lint_and_test.yml` (add new job after `code-style`)

**Step 1: Add type-check job**

Insert this job after the `code-style` job (after line 94) in `.github/workflows/lint_and_test.yml`:

```yaml
  type-check:
    if: github.ref != 'refs/heads/main'
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v6
      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
      - name: Install dependencies
        run: |
          uv venv
          uv sync --locked --dev
      - name: Run ty type checker
        run: uv run ty check apps/
```

Key details:
- `continue-on-error: true` makes this non-blocking (PR checks won't fail)
- Uses the same Python version and uv setup pattern as the `python-tests` job
- Only runs on PRs (not on main pushes) via the `if` condition

**Step 2: Commit**

```bash
git add .github/workflows/lint_and_test.yml
git commit -m "ci: add non-blocking ty type check step"
```

---

## Task 4: Update AGENTS.md with ty command ✅ DONE

**Files:**
- Modify: `AGENTS.md:43-53` (Useful commands section)

**Step 1: Add ty check command**

Add this line after the existing lint/format commands (after "Format python" line):

```markdown
* Type check python: `ty check apps/`
```

**Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: add ty check to AGENTS.md useful commands"
```

---

## Task 5: Enable Phase 1 low-noise rules — unresolved-import ✅ DONE

**Files:**
- Modify: `pyproject.toml` (`[tool.ty.rules]` section)
- Fix: any files with `unresolved-import` violations

**Step 1: Check current violations**

Run:
```bash
uv run ty check --ignore all --error unresolved-import apps/ 2>&1
```

The pre-django-types baseline had 5 violations. Record the new count.

**Step 2: Enable the rule**

In `pyproject.toml`, update the `[tool.ty.rules]` section:

```toml
[tool.ty.rules]
all = "ignore"
unresolved-import = "error"
```

**Step 3: Fix violations**

For each violation, either:
- Fix the import if it's a real issue
- Add `# ty: ignore[unresolved-import]` if it's a conditional/optional import (e.g., `try: import mailchimp3`)

**Step 4: Verify clean**

Run:
```bash
uv run ty check apps/
```
Expected: `All checks passed!`

**Step 5: Commit**

```bash
git add -u
git commit -m "types: enable unresolved-import rule and fix violations"
```

---

## Task 6: Enable Phase 1 low-noise rules — batch of zero/near-zero violation rules ✅ DONE

**Files:**
- Modify: `pyproject.toml` (`[tool.ty.rules]` section)

**Step 1: Identify zero-violation rules**

Run each rule individually to find ones with 0 violations:

```bash
for rule in unresolved-reference unresolved-global invalid-raise call-non-callable duplicate-base cyclic-class-definition cyclic-type-alias-definition conflicting-declarations conflicting-metaclass invalid-base inconsistent-mro static-assert-error subclass-of-final-class; do
  count=$(uv run ty check --ignore all --error "$rule" apps/ 2>&1 | grep -oP 'Found \K\d+' || echo "0")
  echo "$count $rule"
done
```

**Step 2: Enable all zero-violation rules at once**

Add them all to `[tool.ty.rules]` in `pyproject.toml`:

```toml
[tool.ty.rules]
all = "ignore"
unresolved-import = "error"
# Add each zero-violation rule here as = "error"
```

**Step 3: Verify clean**

Run:
```bash
uv run ty check apps/
```
Expected: `All checks passed!`

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "types: enable batch of zero-violation ty rules"
```

---

## Task 7: Enable Phase 1 low-noise rules — remaining low-count rules ✅ DONE

**Files:**
- Modify: `pyproject.toml` (`[tool.ty.rules]` section)
- Fix: files with violations for rules with 1-15 total violations

**Step 1: Check violation counts**

Run each remaining rule to find ones with <=15 violations:

```bash
for rule in missing-argument too-many-positional-arguments unknown-argument invalid-key not-iterable no-matching-overload deprecated empty-body missing-typed-dict-key call-non-callable invalid-raise; do
  count=$(uv run ty check --ignore all --error "$rule" apps/ 2>&1 | grep -oP 'Found \K\d+' || echo "0")
  echo "$count $rule"
done
```

**Step 2: Enable one rule at a time, fix violations, verify clean**

For each rule with <=15 violations, repeat:
1. Add `rule-name = "error"` to `[tool.ty.rules]`
2. Run `uv run ty check apps/` to see violations
3. Fix each violation (real fix or `# ty: ignore[rule-name]` for false positives)
4. Verify `uv run ty check apps/` passes
5. Commit: `git commit -am "types: enable <rule-name> and fix N violations"`

Group rules into a single PR when combined violations are <20.

---

## Task 8: Enable Phase 2 medium-noise rules — one rule per PR ✅ DONE

All Phase 2 rules have been enabled and merged to main.

**Actual results (vs original estimates):**

| Rule | Estimated | Actual violations | Resolution |
|------|-----------|-------------------|------------|
| `unsupported-operator` | ~31 | 16 | Fixed |
| `invalid-return-type` | ~33 | 32 | Fixed |
| `possibly-missing-attribute` | ~43 | 44 | Fixed |
| `invalid-method-override` | ~46 | 60 | Suppressed (intentional overrides) |
| `invalid-parameter-default` | ~79 | 79 | Fixed (Optional annotations) |
| `not-subscriptable` | ~107 | 39 | Suppressed (false positives) |
| `invalid-assignment` | ~109 | 108 | Fixed/suppressed |

**Process for each rule (repeat for each) — preserved for reference:**

Rules enabled in this order:

**For each rule:**

**Step 1: Assess violations**

```bash
uv run ty check --ignore all --error <rule-name> apps/ 2>&1 | head -100
```

Review the violations. Categorize:
- Real bugs to fix
- Django false positives to suppress with `# ty: ignore[<rule-name>]`
- Third-party library false positives to suppress

**Step 2: Enable the rule in pyproject.toml**

```toml
<rule-name> = "error"
```

**Step 3: Fix all violations**

- Fix real bugs
- Add `# ty: ignore[<rule-name>]` for false positives
- Never use blanket `# type: ignore`

**Step 4: Verify clean**

```bash
uv run ty check apps/
```
Expected: `All checks passed!`

**Step 5: Commit and create PR**

```bash
git add -u
git commit -m "types: enable <rule-name> and fix N violations"
```

PR title: `types: enable ty rule <rule-name>`
PR description should list the violation count and categorize fixes vs suppressions.

---

## Task 9: Enable Phase 3 high-noise rules 🔄 IN PROGRESS (branch sk/types-IV)

These rules have the most violations. Handled one at a time in separate commits.

**Actual results:**

| Rule | Estimated | Actual | Status | Branch commit |
|------|-----------|--------|--------|---------------|
| `invalid-type-form` | ~29 | 25 | ✅ Enabled as error, 22 fixed, 3 suppressed | `1b8b42144` |
| `invalid-argument-type` | ~369 | 346 initial; **180 suppressed remain** | ✅ Enabled as error | `3588e5fc9` |
| `unresolved-attribute` | ~2,417 pre-django-types | **1,642 violations** after django-types | ⏳ Enabled as **warn** only | `523b16db7` |

**Session cleanup (2026-03-02, uncommitted):** After enabling these rules, a focused session
removed **43 more ty: ignore suppressions** (395 → 352 total) across 31 files by fixing:
- 14× `get_context_data` LSP violations (team_slug via self.kwargs)
- 7× `create_new_version` LSP violations (aligned signatures with **kwargs)
- 6× return-type/annotation fixes (cast, forward refs, Generator)
- 2× GET[] subscript fixes, 2× uuid default bug, 2× callback param narrowing
- 2× FK _id field trick, 1× cast for SimpleLazyObject, 3× stale suppressions removed

**Next steps for this task:**

1. **Merge this branch** (sk/types-IV) via PR — includes the 3 rule-enablement commits + session cleanup.

2. **Reduce `invalid-argument-type` suppressions (180 remaining):**
   These are primarily Django ORM calls and LangChain API mismatches where ty's type stubs
   don't match the actual runtime types. Approach:
   - Check if django-types or LangChain stubs have improved in newer versions first
   - For genuine Django patterns (QuerySet.filter, model.save, etc.) — suppress with comment
   - For actual bugs — fix

3. **Fix `unresolved-attribute` violations (1,642 as warn):**
   The rule is currently `"warn"` to avoid blocking CI. To promote to `"error"`:
   ```bash
   uv run ty check --ignore all --error unresolved-attribute apps/ 2>&1 | head -100
   ```
   Most violations are Django ORM patterns (`.objects`, `.pk`, custom managers).
   Split by app — some apps may be fixable quickly, others need django-types upstream fixes.

**For `invalid-argument-type` — top offending apps:**

```bash
for app in apps/*/; do
  count=$(uv run ty check --ignore all --error invalid-argument-type "$app" 2>&1 | grep -oP 'Found \K\d+' || echo "0")
  [ "$count" != "0" ] && echo "$count $app"
done
```

**For `unresolved-attribute` — measure current state:**

```bash
uv run ty check --ignore all --warn unresolved-attribute apps/ 2>&1 | tail -3
```

---

## Task 10: Make CI blocking ⏳ PENDING (after unresolved-attribute promoted to error)

**Files:**
- Modify: `.github/workflows/lint_and_test.yml`

**Prerequisite:** All rules from Phases 1-3 that you plan to enforce are enabled and passing.

**Step 1: Remove continue-on-error**

In `.github/workflows/lint_and_test.yml`, change the `type-check` job:

```yaml
  type-check:
    if: github.ref != 'refs/heads/main'
    runs-on: ubuntu-latest
    # Remove: continue-on-error: true
    steps:
      ...
```

**Step 2: Verify CI passes on current main**

Push a test branch and confirm the type-check job passes.

**Step 3: Commit**

```bash
git add .github/workflows/lint_and_test.yml
git commit -m "ci: make ty type checking blocking in CI"
```
