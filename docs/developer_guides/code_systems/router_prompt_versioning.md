# Router Prompt Versioning

The LLM Router node (`RouterNode`) can build its system prompt from a standard template instead of a prompt the user writes. The template is versioned, and each router is pinned to one version. Releasing a new template never changes how an existing router routes: the user chooses when to switch, and tests the new prompt before publishing it.

This builds on [object versioning](versioning.md).

---

## Prompt modes

`RouterNode.prompt_mode` decides where the system prompt comes from:

| Mode | Prompt sent to the model |
|------|--------------------------|
| `standard` | The router's pinned template, filled with the routes and their descriptions. |
| `standard_with_context` | The same, plus the user's `custom_context` under "Additional context". |
| `custom` | The user's `prompt`, followed by the default route. The template is not used. |

A router saved before prompt modes existed has no `prompt_mode`. The node reads it as `custom` (`read_a_router_without_a_mode_as_custom`), so it keeps sending the prompt it was written with. New routers default to `standard`.

Validation follows the mode: `prompt` is checked only in `custom`, and `custom_context` only in `standard_with_context`. The text a mode does not use is kept, so switching back restores it, but it is never validated or sent.

---

## The templates

The templates live in `apps/pipelines/nodes/router_prompts.py`:

- `ROUTER_PROMPT_TEMPLATES` maps a version name (`"v1"`) to its template.
- `LATEST_ROUTER_PROMPT_VERSION` names the version new routers are pinned to.
- `render_router_prompt(version, custom_context, default_route)` fills the template's own placeholders.

A template is filled in two steps:

1. `render_router_prompt` replaces `{custom_context}` and `{default_route}`. The default route's braces are escaped because it is a keyword, not template text. The context is inserted as written, so any variables the user put in it are filled in the next step.
2. The result is passed to `get_system_message` like any other router prompt, which fills `{routes}`, `{routes_list}`, `{participant_data}`, `{temp_state}` and `{session_state}` with Python's `str.format`. Literal braces in a template must therefore be doubled.

---

## How a router is pinned

`RouterNode.prompt_template_version` is an ordinary node param:

- It defaults to `LATEST_ROUTER_PROMPT_VERSION`, so a router created in the builder or through the API is pinned to the latest version.
- A missing or empty value is read as the latest version (`ensure_prompt_template_version_exists`). This covers routers saved before the param existed.
- An unknown version fails validation.
- In the builder, switching a router from `custom` to a standard mode pins it to the latest version if it has none (`RouterPromptModeWidget`). The builder stores params as it sends them, so the widget sets the version rather than relying on the backend default.
- API clients can set it, to upgrade a router or to keep an earlier version.

Because the version is stored on the working node, versioning needs no special handling. Creating a version copies it, reverting restores it, and version comparison shows a change to it like any other param. A router behaves the same in the working version the user tests and in the version they publish.

---

## Upgrading a router

When a router in a standard mode is pinned below the latest version, the builder shows "A newer standard prompt is available" with a **Switch to latest** button. The "Preview generated prompt" dialog then shows the prompt for both the pinned version and the latest, so the user can compare them before switching.

Switching only changes the working version. Published versions keep their template until the user publishes again.

The builder receives all templates and the latest version name through `router_prompt_templates_data()`, rendered as the `router-prompt-templates` JSON script on the pipeline builder page. The preview is rendered by `renderRouterPromptPreview` in `assets/javascript/apps/pipeline/utils.tsx`, which follows the same filling rules as the server. Change both together.

---

## Releasing a new template version

1. Add the new template to `ROUTER_PROMPT_TEMPLATES` under a new name (`"v2"`). Do not edit a released version: routers are pinned to it.
2. Set `LATEST_ROUTER_PROMPT_VERSION` to the new name.
3. Keep the placeholders `{routes}`, `{custom_context}` and `{default_route}`, or update `render_router_prompt` and `renderRouterPromptPreview` together.
4. Add tests in `apps/pipelines/tests/test_router_prompt.py` for the new template's output.

After the release:

- New routers are pinned to the new version.
- Existing routers keep their version, and the builder offers each one the upgrade.
- Routers in `custom` mode are unaffected.

Never remove a version from `ROUTER_PROMPT_TEMPLATES`. A router pinned to a missing version fails validation (`ensure_prompt_template_version_exists`), so it can no longer run.
