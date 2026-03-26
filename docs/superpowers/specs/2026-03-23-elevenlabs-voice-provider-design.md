# ElevenLabs Voice Provider Integration

**Date:** 2026-03-23
**Status:** Draft

## Overview

Integrate ElevenLabs as a voice provider in Open Chat Studio, supporting text-to-speech (TTS), speech-to-text (STT), and instant voice cloning (IVC). Uses the official `elevenlabs` Python SDK and follows existing voice provider patterns.

## Scope

**In scope:**
- TTS via ElevenLabs API (standard HTTP, not streaming)
- STT via ElevenLabs Scribe v2
- Instant Voice Cloning (IVC) via audio file upload
- Dynamic voice catalog sync from ElevenLabs API
- Provider-level model selection (eleven_multilingual_v2, eleven_v3, etc.)

**Out of scope:**
- Voice settings UI (stability, similarity_boost, style, speed)
- WebSocket streaming TTS
- Professional Voice Cloning (PVC)
- ElevenLabs Conversational AI features

## Design

### 1. SpeechService Implementation

New class `ElevenLabsSpeechService` in `apps/service_providers/speech_service.py`:

- `_type: ClassVar[str] = SyntheticVoice.ElevenLabs`
- `supports_transcription: ClassVar[bool] = True`
- Config fields: `elevenlabs_api_key: str`, `elevenlabs_model: str` (default `"eleven_multilingual_v2"`)
- SDK import is lazy (inside methods) to match existing pattern for optional provider deps

**TTS (`_synthesize_voice`):**
- Creates `elevenlabs.client.ElevenLabs` SDK client
- Calls `client.text_to_speech.convert(voice_id=synthetic_voice.external_id, model_id=self.elevenlabs_model, output_format="mp3_44100_128")`
- The SDK `convert()` method returns an iterator of bytes chunks; consume fully via `b"".join(result)` before creating the audio buffer
- Duration calculated via `pydub.AudioSegment` (same pattern as AWS/OpenAI)
- Returns `SynthesizedAudio(audio=..., duration=..., format="mp3")`

**STT (`_transcribe_audio`):**
- Calls `client.speech_to_text.convert(file=audio, model_id="scribe_v2")`
- Returns `result.text`

### 2. Model & Enum Changes

**`SyntheticVoice` model** (`apps/experiments/models.py`):
- New constant: `ElevenLabs = "ElevenLabs"`
- Added to `SERVICES` tuple as `("ElevenLabs", ElevenLabs)`
- Added to `TEAM_SCOPED_SERVICES` list (voices are per-account, custom voices are team-specific)
- New field: `external_id = CharField(max_length=128, null=True, blank=True)` — stores the provider-specific voice identifier when it differs from the display name (e.g., ElevenLabs opaque voice IDs like `"JBFqnCBsd6RMkjVDRZzb"`)
- `external_id` is always populated for ElevenLabs voices; `ElevenLabsSpeechService` reads it directly (no fallback to `name`)
- Existing providers don't use `external_id` — they continue using `name` as the API identifier with no behavior change
- Add a separate `UniqueConstraint` with `condition=Q(external_id__isnull=False)` on `("external_id", "service", "voice_provider")` rather than modifying the existing `unique_together` (since `NULL` values in `unique_together` don't enforce uniqueness in SQL)

**`VoiceProviderType` enum** (`apps/service_providers/models.py`):
- New value: `elevenlabs = "elevenlabs", _("ElevenLabs")`
- `form_cls` property: maps to `forms.ElevenLabsVoiceConfigForm`
- `get_speech_service()`: maps to `speech_service.ElevenLabsSpeechService`

**`VoiceProvider` model** (`apps/service_providers/models.py`):
- `add_files()`: handles ElevenLabs IVC (see Section 4 for full flow)
- `delete()`: cleans up ElevenLabs voices — deletes only IVC-cloned voices from ElevenLabs API (identified by having a `file` FK), then deletes all local `SyntheticVoice` records for this provider. If the API deletion call fails, log a warning and proceed with local deletion.
- New method `sync_voices()`: fetches voices from ElevenLabs API, creates/updates/removes `SyntheticVoice` entries for this provider

### 3. Config Form

**`ElevenLabsVoiceConfigForm`** in `apps/service_providers/forms.py`:
- Extends `ObfuscatingMixin, ProviderTypeConfigForm`
- Fields:
  - `elevenlabs_api_key` — CharField, obfuscated
  - `elevenlabs_model` — ChoiceField with options:
    - `eleven_multilingual_v2` (Multilingual v2 — default)
    - `eleven_v3` (v3 — latest)
    - `eleven_flash_v2_5` (Flash v2.5 — low latency)
    - `eleven_turbo_v2_5` (Turbo v2.5)
  - Model list is a form-level choice, not a DB field — can be updated without migration if ElevenLabs changes models
- `allow_file_upload = True` with file formset for IVC audio uploads (accepts mp3, mp4, wav)

### 4. Voice Sync

**Sync-on-create + manual refresh approach:**

1. **On provider create:** `sync_voices()` fetches all voices from the account via the SDK voices list endpoint and creates `SyntheticVoice` entries:
   - `name` = ElevenLabs voice display name (e.g., "Rachel")
   - `external_id` = ElevenLabs voice_id (e.g., "21m00Tcm4TlvDq8ikWAM")
   - `language` / `language_code` = from voice `labels.language` if available, else empty string
   - `gender` = mapped to closest match from `SyntheticVoice.GENDERS` choices ("male", "female"), or empty string if the label doesn't match a known value
   - `service` = `"ElevenLabs"`
   - `voice_provider` = FK to this provider instance

2. **Manual sync:** "Sync Voices" button on the provider detail page triggers `sync_voices()`. Adds new voices, updates changed names. Voices no longer returned by the API are deleted only if they are not referenced by any experiment (via `synthetic_voice` FK); otherwise they are left in place and skipped.

3. **IVC upload flow:**
   - User uploads audio files via the file formset on the provider form
   - The existing file upload flow creates local `File` model instances
   - `add_files()` receives these `File` instances, reads their content, and sends to ElevenLabs API via `client.voices.ivc.create(name=file.name, files=[file_content])`
   - Creates a `SyntheticVoice` with the returned `voice_id` in `external_id` and the `file` FK pointing to the local `File` record (retained for reference/re-upload)

**Sync trigger points:**
- Provider create view (after save)
- Dedicated POST endpoint for the "Sync Voices" button on the provider detail page

### 5. Dependencies

Add `elevenlabs` package to `pyproject.toml`.

Note: The exact SDK method names (`client.voices.search()`, `client.voices.ivc.create()`, `client.text_to_speech.convert()`, `client.speech_to_text.convert()`) should be verified against the installed SDK version during implementation, as the ElevenLabs Python SDK API surface has changed across versions.

### 6. Testing

- Unit tests for `ElevenLabsSpeechService._synthesize_voice()` — mock SDK client, verify correct params, verify `SynthesizedAudio` returned with consumed iterator bytes
- Unit tests for `ElevenLabsSpeechService._transcribe_audio()` — mock SDK, verify transcript text
- Unit tests for `sync_voices()` — mock SDK voice listing, verify `SyntheticVoice` records created/updated; verify voices in use by experiments are not deleted during sync
- Unit tests for IVC via `add_files()` — mock `client.voices.ivc.create()`, verify `SyntheticVoice` created with correct `external_id` and `file` FK
- Test `VoiceProviderType.get_speech_service()` returns `ElevenLabsSpeechService`
- Test gender mapping — verify unknown gender labels map to empty string
- Migration test — verify `external_id` field added, existing data unaffected

### 7. Files Modified

| File | Change |
|------|--------|
| `apps/service_providers/speech_service.py` | Add `ElevenLabsSpeechService` class |
| `apps/service_providers/models.py` | Add `elevenlabs` to `VoiceProviderType`, update `VoiceProvider` methods |
| `apps/service_providers/forms.py` | Add `ElevenLabsVoiceConfigForm` and file formset |
| `apps/experiments/models.py` | Add `ElevenLabs` constant, `external_id` field, `UniqueConstraint`, update `SERVICES`/`TEAM_SCOPED_SERVICES` |
| `apps/experiments/migrations/` | Migration for `external_id` field and constraint |
| `apps/service_providers/views.py` | Add sync voices endpoint, trigger sync on provider create |
| `pyproject.toml` | Add `elevenlabs` dependency |
| `apps/service_providers/tests/` | Tests for new service, sync, IVC |
