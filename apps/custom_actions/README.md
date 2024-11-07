# Custom Actions App

A 'Custom Action' represents an external HTTP API that can be called by an Experiment.
The Custom Action is defined by an OpenAPI schema that describes the API endpoints and operations associated with the
action.

Each Custom Action can have multiple operations, which represent specific API calls. These individual operations
are what the Experiment is configured through the `CustomActionOperation` model.

When the experiment runs the linked operations are used to created 'tools' which are passed to the LLM and allow it
to interact with the external API.

## Versioning

The `CustomAction` model is not versioned. However, the `CustomActionOperation` model is versioned since this is the
model that is linked to the Experiment.

**Working Version**

When a custom action or operation is first created, it is in a "working version" state. In this
state, the schema is not persisted to the database but is computed dynamically when needed.
This means that changes to the `CustomAction` schema will be reflected in the working version of the Experiment.

**Persisted Version**

When a new version of an experiment is created, the custom action operations are versioned. The
schema for each operation is then persisted to the database, ensuring that the exact state of the operation at the
time of versioning is preserved regardless of changes to the `CustomAction`. It also ensures that the operation is still
valid when the version is created.
