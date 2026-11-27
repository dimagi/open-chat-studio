# Versioning Dev Documentation

See the [**User Documentation**][user-docs] on versioning.

The versioning feature is a working version pattern that allows OCS users to edit chatbot configurations while maintaining immutable snapshots for production use. This ensures that active chatbot sessions for participants continue running on stable chatbot versions while chatbot configuration changes are made in the background.

Versioned objects include Experiment, Pipeline, Node, Collection and more. To confirm the current implementation, review the codebase and technical documentation.
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio)


---

## Versioning Terminology

| Term              | [User-Facing Term][user-docs]        | Description                                                                 |
|-------------------|-------------------------|-----------------------------------------------------------------------------|
| **Working version** | *Unreleased version*     | The editable version of an object.                                 |
| **Default version** | *Published version*      | The version currently live and user-facing.                         |
| **Version family**  | —                       | A group of instances that are versions of the same working instance. This includes the working version itself. |

---

## How to Think About Versioning

Users are always working on the **latest version** of their chatbot. When they create a new version, this freezes their current progress and assigns a version number to it. Any new edits are made on the next version.

### Technically

- All versioned objects have a `working_version` field, which is a foreign key to an instance of the same model.
- Creating a new version means **duplicating** the `working version`. All objects that contribute to the behavior of the chatbot are also versioned and linked to the new version. The exception to this is global objects such as LLM providers which are never versioned.
- The duplicated object’s `working_version` points to the original object.
- For objects like Experiments and Pipelines which have version numbers, the following applies:
    - The newly created version gets the current version number.
    - The working version's version number is incremented.

---

## How to Version a Model

A versioned model declares, once, how it travels between its working row and its versions. The
declaration is a `VersionSpec` (`apps/experiments/versioning.py`), and the publish, copy and revert
operations are generated from it. To version a model:

1. Inherit from the mixins:
    - `VersionsMixin` - the version operations and the family helpers (`get_working_version`,
      `latest_version`, `is_a_version`).
    - `VersionsObjectManagerMixin` on the model's manager - excludes archived rows by default and
      annotates querysets with `is_version`.

2. Add the family columns:
    - `working_version`: a nullable foreign key to itself, `related_name="versions"`.
    - `is_archived`: a `BooleanField`.
    - `version_number`: (optional) a `PositiveIntegerField`, for top-level models whose versions
      users see numbered. The spec assigns the working number to each snapshot and advances the
      working row.

3. Declare a `VersionSpec` next to the model:

    ```python
    class WidgetVersionSpec(VersionSpec):
        class Meta:
            model = "widgets.Widget"
            identity = frozenset(
                {"id", "team", "created_at", "updated_at", "working_version", "version_number", "is_archived", "public_id"}
            )
            regenerated = {"public_id": uuid4}

        template = Reference(Strategy.NEW_VERSION, owned=True, provides="template")
        collection = Reference(Strategy.LIVE_REFERENCE)
    ```

    - `Meta.identity` names the fields that never travel: the row's own identity, the family
      columns and lifecycle state. Every other concrete field is a row field and travels by being
      cloned, so a new field is versioned content unless you add it to `identity`. Cloning still
      copies an identity column onto the snapshot (`team` above stays the working row's); `identity`
      keeps the generic revert from writing it, and only `regenerated`, the framework (the family
      columns, the version number) or a hook replaces the cloned value.
    - `Meta.regenerated` gives an identity field a fresh value on the clone before its first save,
      which is how a `unique=True` column such as `public_id` survives cloning.
    - A `Reference` member declares how a foreign key to another versioned model travels on publish
      (its `Strategy`), whether the target is `owned` (so a copy copies it and a revert resets it in
      place), and what it `provides` to members declared after it. An `OwnedSet` member carries rows
      reached through reverse accessors. A `ParamReference` member is an id inside a JSON field
      (`Node.params["source_material_id"]`), with the FK column that mirrors it and an `Archiving`
      policy for when a version holding it is archived. Members run in class-body order; a member that `reads` a
      key no earlier member `provides` raises `VersionSpecError`, and the surrounding transaction
      rolls back the clone and the advanced version number.
    - Four hooks are the only place model-specific imperative code remains. `on_clone(working, clone,
      **options)` runs on the clone before its first save (a parent to set, a key to rename);
      `on_publish(working, version, **options)` and `on_copy(working, copy, **options)` run after the
      members and before the snapshot's final save; `on_revert(working, version)` runs after the
      working row and its members are written. `**options` are what the spec's `publish` or `copy`
      was called with: the mixin's `create_new_version()` passes none, so a model that takes options
      keeps a wrapper that forwards them (`Experiment.create_new_version(version_description=...,
      make_default=..., name=...)`). A spec may also narrow `members_for(row)`, as `Node`'s does to
      the params the row's type declares.

4. Run `manage.py check`. The `versioning.*` system checks (`apps/experiments/checks.py`) reject a
   member that names nothing on the model, a foreign key to a versioned model that no member
   declares, a `reads` that nothing provides, and identity names that are not fields. A versioned
   model that lacks the family columns is reported too.

5. Implement `version_details`: see the [VersionDetails section](#the-versiondetails-class).

6. Filter returned objects to the UI: be sure to only return working versions to users.

Calling `create_new_version()` on a spec-bound model publishes it; `create_new_version(is_copy=True)`
copies it; `revert_to_version(version)` resets the working row. A model that has no spec yet falls
back to cloning its row, and should get a spec before it grows references of its own.

The shared test helpers in `apps/utils/tests/versioning_helpers.py` (`assert_snapshot`,
`assert_family`) assert, in both directions, which fields a version carries.

## The `VersionDetails` class

The `VersionDetails` class is a core component of the versioning system that encapsulates the version-specific information of a model instance. It consists of:

- `instance`: The model instance being versioned
- `fields`: A list of `VersionField` objects representing the versioned fields
- `fields_changed`: A boolean indicating if any fields have changed
- `fields_grouped`: Property that groups fields by their `group_name` for organized display

Key methods:

- `compare(previous_version_details)`: Compares the current instance with a previous version, tracking changes
- `get_field(field_name)`: Retrieves a specific field by name

Each `VersionField` represents a field or property of the model and can handle:

- Primitive values (strings, numbers, etc.)
- Querysets (collections of related objects)
- Versioned models (models that implement `version_details`)
- Unversioned models (regular Django models)

When implementing versioning for a model, you must provide a `version_details` property that returns a `VersionDetails` instance with the fields you want to track.

Example:
```python
def _get_version_details(self) -> VersionDetails:
    return VersionDetails(
        instance=self,
        fields=[
            VersionField(group_name="General", name="name", raw_value=self.name),
            VersionField(group_name="Settings", name="enabled", raw_value=self.enabled),
        ],
    )
```

## Archiving
An object can only be archived if neither its working version nor any of its other versions are used by any published version of a related object.

If the object is in use, the user should be informed of the specific usages and prompted to archive those related objects first. For reference, see `Collection.archive` (`apps/documents/models.py`), which refuses and returns `False` while a live node or experiment still references it.


[user-docs]: https://docs.openchatstudio.com/concepts/versioning/
