# Versioning System

This project includes a custom data versioning system to allow users to version their chatbots.

A versioned model declares how it travels between its working row and its versions in a
`VersionSpec` (`apps/experiments/versioning.py`); publish, copy and revert are generated from that
declaration, and `manage.py check` reports a declaration that does not match the model. The main
entry point is `apps.experiments.Experiment.create_new_version`, whose spec is
`ExperimentVersionSpec` beside the model; `PipelineVersionSpec` and `NodeVersionSpec` live in
`apps/pipelines/versioning.py`. The smaller versioned models keep their `create_new_version`
overrides, and a field added to one of those has to be handled in that override until the model
is moved. A node param that names a database record is a `ParamReference` member of
`NodeVersionSpec`; `Node.archive` and `Node.unarchive` read the same members.

When adding a field to a versioned model, decide whether it is versioned content (the default: it
travels) or identity (add it to the spec's `Meta.identity`). When adding a foreign key to another
versioned model, declare a `Reference` member for it; the `versioning.E002` check refuses an
undeclared one.

See the [developer docs](../developer_guides/code_systems/versioning.md)
