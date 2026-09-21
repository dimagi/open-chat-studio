> Favor precision over recall: only raise an issue when you are confident it is a real defect, and stay silent when the surrounding context is unclear. `pyproject.toml` is a small file that changes rarely, so a false alarm here is expensive.

#### Dependency Declarations
- A new entry in `[project] dependencies` or in a `[dependency-groups]` group is a new third-party dependency, which this project requires a human to agree to before it lands. Flag it as something the reviewer must confirm was discussed, naming the package
- The same distribution declared twice, either within one list or across `[project] dependencies` and a `[dependency-groups]` group. Extras count: `celery` and `celery[redis]` are one distribution, and listing both is redundant
- A dependency placed in the wrong list: runtime code under `apps/` or `config/` importing a package that is only declared in the `dev` or `docs` group, or test/lint/docs tooling declared in `[project] dependencies` where it ships to production
- A version constraint added without a stated reason. The convention here is a bare package name, letting `uv.lock` pin the resolved version; a `>=`, `<` or `==` bound means something specific broke, so it needs a comment saying what. Do not flag existing bare names for lacking a constraint, and do not flag existing bounds that the diff only moves
- A dependency source that is not PyPI (a git URL, a direct URL, a local path, or a `[[tool.uv.index]]` entry) — and any credential embedded in such a URL

#### Cross-File Consistency
- `[tool.uv] required-version` is mirrored in `Dockerfile` and `Dockerfile.dev`, which pin the `ghcr.io/astral-sh/uv` image tag. A change to one without the others makes the local and built environments disagree
- `[project] requires-python`, `[tool.ruff] target-version` and `[tool.ty.environment] python-version` all name the Python version. A change to one that leaves the others behind means the linter or the type checker is now reasoning about a different language version than the code runs on
- The linters and type checker are pinned twice: here (`ruff` and `ty` in `[dependency-groups] dev`, `djlint` in `[project] dependencies`) and as a `rev` in `.pre-commit-config.yaml`. Only `ty` carries a comment saying to keep the two in sync; `ruff` and `djlint` have the same split without one. A bump on either side alone means `uv run inv ruff`, `uv run inv typecheck` and the pre-commit hook run different versions, so the same code passes locally and fails in CI. Nothing enforces this for the Python tools; the `eslint-versions-in-sync` hook covers only the JS side
- Read the file with `file_read` before reporting a mismatch of this kind; do not report one inferred from the diff alone

#### Lint and Type-Check Configuration
- A rule added to `[tool.ruff.lint] ignore`, to `extend-per-file-ignores`, or to `[tool.ruff] exclude` without a comment saying why. Each existing entry carries one, and an unexplained suppression is indistinguishable from silencing a real finding
- A rule removed from `[tool.ty.rules]`, or moved from `error` to `ignore`. These are enabled to hold a zero-violation floor, so relaxing one gives up that guarantee
- An entry removed from `[tool.ruff.lint.flake8-tidy-imports] banned-module-level-imports`. Those packages are expensive to import and are deliberately loaded inside functions; un-banning one puts the import cost back into startup
- An entry added to `runtime-evaluated-base-classes` or `banned-module-level-imports` is fine, but should say what it is for when the reason is not obvious from the name

#### Test Configuration
- An `ignore` line added to `[tool.pytest.ini_options] filterwarnings` that is not scoped to a specific message, or that suppresses a warning raised by this project's own code rather than by a dependency. The `error::DeprecationWarning:apps\..*` entries exist so that a deprecation against our own call site fails the test. pytest applies filters in order and the last match wins, so an ignore placed below those lines, and broad enough to match the same warning, silently exempts our own code again
- A change to `addopts` that widens what runs by default, in particular dropping `-m "not integration and not eval"`, which would make CI run tests that need API keys
- A new marker used in tests but not registered under `markers`; `--strict-markers` turns that into a hard error

#### Do Not Report
- The `version = "0.1.0"` field. Releases are git tags and the running version is resolved at build time; the comment above the field says so
- Ordering or grouping of the dependency lists — they are not alphabetised, and reordering them is churn
- Missing packaging metadata (`classifiers`, `keywords`, `authors`). This is an application, not a published distribution
