# Review guidelines

Instructions for automated code reviewers (OpenCodeReview, Claude Code Review, CodeRabbit). Human contributors can use it as a checklist.

## Before flagging

* Read the "Invariants" section of `AGENTS.md`. It lists facts reviewers repeatedly get wrong (PostgreSQL only, generated files, default managers that filter archived records, the autouse team fixture, where the changelog lives). Do not raise a finding that an invariant already answers.
* Do not propose hand-edits to generated files. Point at the source that produces them (see `AGENTS.md`).
* Do not review for Python versions below 3.13.

## Path-specific checklists

These rule files are the single source for per-path checks. Apply the one matching each changed file.

| Changed path | Rule file |
|---|---|
| `**/migrations/*.py` | `.opencodereview/rules/django_migrations.md` |
| `apps/**/models.py` | `.opencodereview/rules/django_models.md` |
| `templates/**/*.html` | `.opencodereview/rules/django_templates.md` |
| `**/pyproject.toml` | `.opencodereview/rules/pyproject_toml.md` |
| `**/.pre-commit-config.yaml` | `.opencodereview/rules/pre_commit_config.md` |

## Additional checks

* **Startup time.** Flag heavy AI/ML imports at module level (`langchain_*`, `openai`, `anthropic`, `google`, `boto3`, `pandas`, `numpy`). Import them inside the functions that use them.
* **Tests.** Flag tests that depend on migration-seeded global rows (`LlmProviderModel`, `PricingRule`); the suite runs with `--reuse-db`, which makes them order-dependent.
* **Docs and ADRs (`docs/**/*.md`).** Cross-check status claims and decision IDs against the code in the diff. Verify that referenced issue and PR numbers are still open and cover the work deferred to them. Stale cross-references are the highest-value finding in this tree.
