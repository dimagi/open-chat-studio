# Versioning
To version a model, we keep track of the original model by adding a `working_version` field on the model. This field is a FK to the object that the user sees and changes. When they create a new version, we copy the `working_version` as a new version of itself.

## Things to keep in mind when versioning a model
### On the model side
1. Inherit from `VersionsMixin`

Your versioned model should inherit from `VersionsMixin`, which will add useful methods to your model.

2. Add a `working_version` field

This field should be a self-referencing foreign key (FK) to the model. For reference, see the Experiment model, which uses this approach.

3. Add an `is_archived` field

We probably don't want to delete objects that have versions, because a version of it might still be useful to users. We thus archive these objects instead of deleting them.
 
4. Consider changing fields between versions

The only fields that we expect to change when creating a new version are those relating to versioning. These are considered "internal" fields, like `created_at`, `version_number` etc. If you added new fields that will differ when creating a new version, you should override the `get_fields_to_exclude` method (from the `VersionsMixin`) to include these new fields. This way we can accurately determine if there are meaningful changes between the working and the latest versions.


### View implementation
1. Archive instead of delete

Deleting an object should archive it instead of removing it from the database.

2. Exclude versioned and archived records

We probably only want the user to see objects that they can mutate. The exception is the versions list we show in the experiments home page. Your object's object manager should inherit from `VersionsObjectManagerMixin` that filters out archived versions by default. Use the `is_version` annotation to filter out versions from the queryset that returns a list of your model to the user.