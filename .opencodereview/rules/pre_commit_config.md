> Favor precision over recall: only raise an issue when you are confident it is a real defect, and stay silent when the surrounding context is unclear. Dependabot's own PRs are not reviewed by this tool, so any change you see here is a deliberate hand edit of a file that changes a few lines at a time.

#### Version Pins
- `ruff`, `ty` and `djlint` are pinned here as a `rev` and again in `pyproject.toml` (`ruff` and `ty` in `[dependency-groups] dev`, `djlint` in `[project] dependencies`). Only the `ty` entry carries a comment saying to keep the two in sync. A bump on one side alone means the hook and `uv run inv ruff` / `uv run inv typecheck` run different versions, so the same code passes locally and fails in CI
- The `eslint` and `prettier` mirrors are pinned against `package.json` and `pnpm-lock.yaml`. `scripts/check_eslint_versions.py` enforces this for eslint only; a `prettier` bump has no checker behind it
- `rev` must name an immutable tag or a full commit SHA. A branch name such as `main` makes the hook resolve to different code over time, and pre-commit will not warn
- `default_language_version.python` should match `[project] requires-python` in `pyproject.toml`
- Read the other file with `file_read` before reporting a mismatch of this kind; do not report one inferred from the diff alone

#### Hook Scope
- `files:` and `exclude:` are Python regexes, not globs. A value written as a glob (`*.py`, `apps/*`) is still a valid regex and matches something other than what was intended; one that matches nothing silently disables the hook with no error
- A widened `exclude:` drops files from a check. It needs a comment saying what is being exempted and why, the way the existing `docs/plans/` and `/migrations/` exclusions do
- CI runs the hooks through `prek-action` with `--files <changed files>` (`.github/workflows/lint_and_test.yml`), so a hook only ever sees what the PR touched. A check that has to look at the whole repository to be correct must set `always_run: true` and `pass_filenames: false`, or a violation introduced outside the diff will pass
- `always_run: true` on a hook that only needs the changed files makes every commit and every PR pay for it

#### Local Hooks
- The path in `entry:` must exist under `scripts/` and be runnable as written. `language: system` runs it against whatever is on PATH, with no environment of its own, so a script that imports a third-party package belongs on `language: python` with `additional_dependencies` instead
- `name:` is the only thing a contributor sees when the hook fails, so it should state the rule being enforced rather than the script being run
- `fail_fast: true` stops every later hook in the run. Adding it to a second hook means a contributor fixes one failure at a time instead of seeing them together

#### Do Not Report
- A `rev` being behind the upstream release. Dependabot raises those bumps
- The order of `repos:` entries
- Missing `stages:`, `types:` or `name:` on a third-party hook; those come from the upstream hook definition
