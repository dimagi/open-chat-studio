# Pipeline Repository Pattern

All DB access during pipeline execution goes through `ORMRepository` (`apps/pipelines/repository.py`).

## Architecture

```text
bots.py  →  LangGraph config  →  base.py extracts repo  →  node.repo
            {"configurable":       self._repo = config     self.repo.get_llm_service(...)
              {"repo": ORMRepository()}}
```

* `ORMRepository` — production, wraps Django ORM
* `InMemoryPipelineRepository` — tests, subclass of `ORMRepository` backed by dicts and `factory_boy.build()`

## Using `self.repo` in nodes

`self.repo` is available in any `_process()` or `_process_conditional()` method:

```python
# In a node method
service = self.repo.get_llm_service(self.llm_provider_id)

# In a mixin (mixed into node classes)
messages = self.repo.get_session_messages(session, self.get_history_mode())

# In a free function that receives the node
def _get_search_tool(node: PipelineNode):
    collections = node.repo.get_collections_for_search(node.collection_index_ids)
```

Pass `repo` explicitly to shared utilities:
```python
PromptTemplateContext(session, repo=self.repo)
PipelineParticipantDataProxy(output_state, session, repo=self.repo)
```

## Adding a new DB operation

1. Add the method to `ORMRepository` (wrap ORM call, catch `DoesNotExist` → raise `RepositoryLookupError`)
2. Override in `InMemoryPipelineRepository` (use dict lookup or pre-loaded data)
3. Add tests for both implementations in `test_repository.py`
4. Use `self.repo.new_method(...)` in the node

## Error handling

All lookup failures raise `RepositoryLookupError` (not Django's `DoesNotExist`):
```python
try:
    material = self.repo.get_source_material(self.source_material_id)
except RepositoryLookupError:
    raise PipelineNodeBuildError("Source material not found")
```

## Writing tests

Use `InMemoryPipelineRepository` for pure-logic tests (no DB needed):
```python
def test_my_node():
    repo = InMemoryPipelineRepository()
    repo.participant_schedules = [{"name": "Test"}]  # pre-load data
    node = MyNode(name="test", node_id="123", django_node=None)
    state = PipelineState(
        messages=["hi"], outputs={},
        experiment_session=ExperimentSessionFactory.build(),
    )
    config = {"configurable": {"repo": repo}}
    output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
```

Use `ORMRepository` for integration tests that need real DB records:
```python
@pytest.mark.django_db()
def test_my_integration(experiment_session):
    config = {"configurable": {"repo": ORMRepository()}}
    # ...
```

## Out of scope
* **`bots.py` finalization** — runs after pipeline execution, outside the node boundary
* **`AssistantAdapter` FK traversals** — deprecated node, deferred
