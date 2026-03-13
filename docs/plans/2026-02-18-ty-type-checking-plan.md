# ty Type Checking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Incrementally adopt ty type checking across the Open Chat Studio codebase, starting with foundation tooling and progressively enabling rules from low-noise to high-noise.

**Architecture:** Install `django-types` for Django stub support, configure ty in `pyproject.toml` with all rules ignored, add non-blocking CI, then enable rules tier-by-tier in separate PRs. Each rule enablement PR fixes all violations for that rule.

**Tech Stack:** ty (Python type checker), django-types (Django stubs), uv (package manager), GitHub Actions CI

**Design doc:** `docs/plans/2026-02-18-ty-type-checking-design.md`

---

## Task 1: Install django-types and ty as dev dependencies

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

## Task 2: Configure ty in pyproject.toml with all rules ignored

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

## Task 3: Add non-blocking ty CI step

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

## Task 4: Update AGENTS.md with ty command

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

## Task 5: Enable Phase 1 low-noise rules — unresolved-import

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

## Task 6: Enable Phase 1 low-noise rules — batch of zero/near-zero violation rules

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

## Task 7: Enable Phase 1 low-noise rules — remaining low-count rules

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

## Task 8: Enable Phase 2 medium-noise rules — one rule per PR

Each of these rules has 30-110 violations. Handle them as separate PRs.

**Process for each rule (repeat for each):**

Rules to enable in this order:
1. `unsupported-operator` (~31 violations)
2. `invalid-return-type` (~33 violations)
3. `possibly-missing-attribute` (~43 violations)
4. `invalid-method-override` (~46 violations)
5. `invalid-parameter-default` (~79 violations)
6. `not-subscriptable` (~107 violations)
7. `invalid-assignment` (~109 violations)

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

## Task 9: Enable Phase 3 high-noise rules

These rules have the most violations. Handle them one at a time, potentially splitting into multiple commits within a PR.

**Rules:**
1. `invalid-argument-type` (~369 violations)
2. `invalid-type-form` (~29 violations)
3. `unresolved-attribute` (~2,417 violations pre-django-types; re-measure)

**For `invalid-argument-type` and `invalid-type-form`:**

Follow the same process as Task 8. For large violation counts, split fixes by app directory:

```bash
# Check per-app violation count
for app in apps/*/; do
  count=$(uv run ty check --ignore all --error invalid-argument-type "$app" 2>&1 | grep -oP 'Found \K\d+' || echo "0")
  [ "$count" != "0" ] && echo "$count $app"
done
```

Consider splitting into multiple commits (one per app) within the same PR.

**For `unresolved-attribute`:**

This is the largest rule. After django-types is installed (Task 1), re-measure:

```bash
uv run ty check --ignore all --error unresolved-attribute apps/ 2>&1 | tail -3
```

If still >500 violations, consider:
- Enable as `"warn"` first instead of `"error"`
- Fix app-by-app in separate PRs
- Some may require upstream django-types fixes

---

## Task 10: Make CI blocking

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
