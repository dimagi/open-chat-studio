# Versioning Dev Documentation

See also the [**User Documentation**][user-docs] on versioning.

---

## Versioning Terminology

| Term              | User-Facing Term        | Description                                                                 |
|-------------------|-------------------------|-----------------------------------------------------------------------------|
| **Working version** | *Unreleased version*     | The editable versio of an object.                                 |
| **Default version** | *Published version*      | The version currently live and user facing.                         |
| **Version family**  | —                       | A group of instances that are versions of the same working instance. This includes the working version itself |

---

## How to Think About Versioning

Users are always working on the **latest version** of their chatbot. When they create a new version, it is really only freezing their progress and assigning a version number to it. Any new edits will be made on the next version.

- All versioned objects have a `working_version` field, which is a foreign key to an instance of the same model.
- Creating a new version means **duplicating** the `working version`. All objects that contribute to the behavior of the chatbot are also versioned and linked to the new version. The exception to this is global objects such as LLM providers which are never versioned.
- The duplicated object’s `working_version` points to the original object.
- For objects like Experiments and Pipelines which have version numbers, the following applies:
    - The newly created version gets the current version number.
    - The working version's version number is incremented.

---

## How to Version a Model

If you add a model that needs to be versioned, you generally need to do the following:

1. Inherit from mixins:
    - [`VersionsMixin`](https://github.com/dimagi/open-chat-studio/blob/94569be2a5af5860682a1ac2ba829129ea0d3fc6/apps/experiments/versioning.py) - Adds utility methods for working with versions.
    - [`VersionsObjectManagerMixin`](https://github.com/dimagi/open-chat-studio/blob/94569be2a5af5860682a1ac2ba829129ea0d3fc6/apps/experiments/versioning.py) - This ensures that archived objects are excluded by default and helpful annotations are added to querysets of your model.

2. Add these fields to your model:
    - `working_version`: A nullable foreign key to itself
    - `is_archived`: A `BooleanField` indicating whether or not this instance is archived
    - `version_number`: (optional) `IntegerField' used to track the objects version number. This is only really necessary for top level objects.

3. Implement `version_details`: See the [VersionDetails section](#the-versiondetails-class)
4. Filter returned objects to the UI: Be sure to only return working versions to users.

If the model can be linked to a Pipeline Node, be sure to version this object whenever the node is versioned. The same goes for archiving. Whenever this node is archived and it is a versioned node, we need to make sure that all related versioned models are archived as well.

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
        ]
    )
```

## Archiving
An object can only be archived if neither its working version nor any of its other versions are used by any published version of a related object.

If the object is in use, the user should be informed of the specific usages and prompted to archive those related objects first. For reference, see the behavior when attempting to archive an `OpenAiAssistant`.


[user-docs]: https://docs.openchatstudio.com/concepts/versioning/
