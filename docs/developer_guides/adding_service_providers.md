# Adding a Service Provider

This guide covers adding a new provider to an existing category (e.g. a new LLM, voice, or messaging provider), and briefly, adding a whole new provider category. For the conceptual overview of the framework, see `apps/service_providers/README.md`.

## Architecture recap

There are five provider categories, each backed by one team-scoped model with an encrypted JSON `config` field:

| Category | Model | Type enum | Service base class |
|---|---|---|---|
| LLM | `LlmProvider` | `LlmProviderTypes` | `llm_service/main.py::LlmService` |
| Voice | `VoiceProvider` | `VoiceProviderType` | `speech_service.py::SpeechService` |
| Messaging | `MessagingProvider` | `MessagingProviderType` | `messaging_service.py::MessagingService` |
| Auth | `AuthProvider` | `AuthProviderType` | `auth_service/main.py::AuthService` |
| Tracing | `TraceProvider` | `TraceProviderType` | `tracing/base.py::Tracer` |

All models and enums live in `apps/service_providers/models.py`. The type enum is the contract: each member routes to a config form (via the `form_cls` property) and a service class (via a factory method like `get_speech_service()`). Views, URLs, and tables are generic and driven off these enums — adding a provider normally requires no view or template changes.

## Adding a provider to an existing category

Using a new voice provider as the running example. The ElevenLabs provider (PR #3078) is a complete real-world reference.

### 1. Add the enum member and routing

In `apps/service_providers/models.py`:

```python
class VoiceProviderType(models.TextChoices):
    ...
    elevenlabs = "elevenlabs", _("ElevenLabs")
```

Add a `case` to the enum's `form_cls` property and to its service factory method (`get_speech_service`, `get_llm_service`, `get_messaging_service`, `get_auth_service`, or `get_service` depending on category).

### 2. Add the config form

In `apps/service_providers/forms.py`, subclass `ProviderTypeConfigForm`. Form field names must match the service class's constructor fields, since the form's cleaned data is stored as the provider `config` and passed to the service as kwargs.

```python
class ElevenLabsVoiceConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["elevenlabs_api_key"]

    elevenlabs_api_key = forms.CharField(label=_("API Key"))
```

Use `ObfuscatingMixin` with `obfuscate_fields` for secrets — it masks values on display and preserves the stored secret when the user saves without retyping it.

### 3. Implement the service class

Subclass the category's service base class. Most are pydantic models constructed with the provider config as kwargs. Key methods per category:

* **LLM** (`LlmService`): override `_chat_model()` (not `get_chat_model()`, which wraps it to stamp cost-tracking metadata), plus capability flags like `supports_transcription` / `supports_assistants`.
* **Voice** (`SpeechService`): set `_type` to a `SyntheticVoice` service constant and implement `_synthesize_voice()`; optionally `_transcribe_audio()`.
* **Messaging** (`MessagingService`): set `supported_platforms`, implement `send_text_message()` / `send_voice_message()`, and webhook management if supported.
* **Auth** (`AuthService`): implement `_get_http_client_kwargs()` returning httpx auth arguments.
* **Tracing** (`Tracer`): implement the abstract trace/span context managers and callbacks.

If the provider needs an SDK, add it to `pyproject.toml` and import it lazily inside the service class.

### 4. Create a migration

Adding an enum member widens the model's `type` choices, so run `makemigrations service_providers` to generate the `AlterField` migration.

### 5. Category-specific extras

* **LLM**: add default models to `DEFAULT_LLM_PROVIDER_MODELS` in `llm_service/default_models.py` and seed them via a data migration — see [Managing LLM Models](managing_models.md).
* **Voice**: add a service constant to `SyntheticVoice` in `apps/experiments/models.py` (including `SERVICES`, and `TEAM_SCOPED_SERVICES` if voices are per-team). If the provider needs post-create side effects such as syncing its voice list, use `VoiceProvider.run_post_save_hook()`.
* **Messaging**: hook the new service into channel handling in `apps/channels/` if it supports a new platform.

### 6. Tests

Add tests in `apps/service_providers/tests/` (each category has a test file, e.g. `test_voice_providers.py`). Factories live in `apps/utils/factories/service_provider_factories.py`.

### 7. Optional: gate availability

To hide the provider behind a feature flag or setting, add a condition in `get_available_subtypes()` in `apps/service_providers/utils.py` (e.g. `openai_voice_engine` requires a waffle flag, Slack requires `settings.SLACK_ENABLED`).

## Adding a new provider category

This is rare. In addition to the steps above you need:

1. A new model in `models.py` inheriting `BaseTeamModel` with `type`, `name`, and `config = encrypt(models.JSONField(default=dict))` fields, plus a slug in `const.py`.
2. A new type enum with `form_cls` and a service factory method.
3. A new member on the `ServiceProvider` enum in `utils.py` — this drives the generic views, tables, and URLs.
4. A UI entry point: include `service_providers/service_provider_home.html` with your `provider_type` in `templates/teams/manage_team.html`.
5. Permissions for the new model (the generic views check `<action>_<model>` permissions via `ServiceProvider.get_permission()`).
