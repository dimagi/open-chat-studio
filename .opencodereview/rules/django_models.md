> These checks are additional to the general Python review. Only raise an issue you are confident is a real defect; confirm the base classes and managers with `file_read` before reporting one.

#### Team Scoping
- A new model holding team data that does not subclass `BaseTeamModel`. Every tenant-scoped table carries a `team` FK
- A manager method, queryset or `related_name` traversal that can return rows from another team
- A `ForeignKey` to a team-scoped model from a model that is not itself team-scoped

#### Auditing
- A field added to a model decorated with `@audit_fields` but not to that app's `model_audit_fields.py`, so the change goes untracked
- A model with `@audit_fields(..., audit_special_queryset_writes=True)` whose manager does not subclass `AuditingManager`

#### Versioning
`Experiment` and `Pipeline` are versioned; their default managers filter `is_archived=False`, and `is_editable` is `not is_archived`.
- A new field on a versioned model that `create_new_version` and the version comparison do not account for
- Code reaching for `_base_manager` or `objects.all()` in a way that reintroduces archived rows
- A view that re-checks `is_archived` after loading through `objects`, which already excludes them

#### Do Not Report
- Missing docstrings, `Meta.ordering`, or `__str__`
- `Meta.fields`/`list_display` style lists not being annotated `ClassVar` — RUF012 is off here
