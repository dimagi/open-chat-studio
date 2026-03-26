# ElevenLabs Voice Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ElevenLabs as a voice provider supporting TTS, STT, and instant voice cloning.

**Architecture:** Follows the existing `SpeechService` subclass pattern. Adds `ElevenLabsSpeechService` for TTS/STT, extends `SyntheticVoice` with an `external_id` field for opaque voice identifiers, and implements dynamic voice sync from the ElevenLabs API on provider creation with a manual refresh endpoint.

**Tech Stack:** Python, Django, `elevenlabs` SDK, `pydub` for audio duration, pytest for testing.

**Spec:** `docs/superpowers/specs/2026-03-23-elevenlabs-voice-provider-design.md`

---

### Task 1: Add `elevenlabs` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependency**

Add `elevenlabs` to the project dependencies in `pyproject.toml` under `[project.dependencies]`.

```toml
"elevenlabs>=1.0",
```

- [ ] **Step 2: Install and verify**

Run: `uv sync`
Expected: Package installs successfully.

- [ ] **Step 3: Verify SDK API surface**

Run a quick check to confirm the SDK method names match what the spec expects:

```bash
uv run python -c "
from elevenlabs.client import ElevenLabs
import inspect
client = ElevenLabs.__new__(ElevenLabs)
# Check TTS methods
print('TTS methods:', [m for m in dir(client.text_to_speech) if not m.startswith('_')])
# Check voice methods
print('Voice methods:', [m for m in dir(client.voices) if not m.startswith('_')])
"
```

Document the actual method names if they differ from `client.text_to_speech.convert()`, `client.voices.search()`, `client.voices.ivc.create()`, `client.speech_to_text.convert()`. Update subsequent tasks accordingly.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add elevenlabs SDK dependency"
```

---

### Task 2: Add `external_id` field to `SyntheticVoice` and ElevenLabs constants

**Files:**
- Modify: `apps/experiments/models.py:369-442`
- Create: `apps/experiments/migrations/XXXX_add_external_id_to_syntheticvoice.py` (auto-generated)
- Test: `apps/service_providers/tests/test_voice_providers.py`

- [ ] **Step 1: Write failing test for external_id field**

Add to `apps/service_providers/tests/test_voice_providers.py`:

```python
@pytest.mark.django_db
def test_synthetic_voice_external_id(team_with_users):
    """SyntheticVoice should support an external_id field for opaque provider voice identifiers"""
    from apps.utils.factories.service_provider_factories import VoiceProviderFactory

    provider = VoiceProviderFactory(team=team_with_users)
    voice = SyntheticVoice.objects.create(
        name="Rachel",
        external_id="21m00Tcm4TlvDq8ikWAM",
        neural=True,
        language="English",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    voice.refresh_from_db()
    assert voice.external_id == "21m00Tcm4TlvDq8ikWAM"
    assert voice.name == "Rachel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_synthetic_voice_external_id -v`
Expected: FAIL — `SyntheticVoice` has no `ElevenLabs` constant or `external_id` field.

- [ ] **Step 3: Add ElevenLabs constant and external_id field**

In `apps/experiments/models.py`, modify the `SyntheticVoice` class:

```python
# Add constant after OpenAIVoiceEngine = "OpenAIVoiceEngine"
ElevenLabs = "ElevenLabs"

# Update SERVICES tuple — add after OpenAIVoiceEngine entry
SERVICES = (
    ("AWS", AWS),
    ("Azure", Azure),
    ("OpenAI", OpenAI),
    ("OpenAIVoiceEngine", OpenAIVoiceEngine),
    ("ElevenLabs", ElevenLabs),
)

# Update TEAM_SCOPED_SERVICES — add ElevenLabs
TEAM_SCOPED_SERVICES = [OpenAIVoiceEngine, ElevenLabs]
```

Add the `external_id` field after the `name` field:

```python
external_id = models.CharField(
    max_length=128,
    null=True,
    blank=True,
    help_text="Provider-specific voice identifier when it differs from the display name",
)
```

Add a `UniqueConstraint` in the `Meta` class (keep the existing `unique_together`):

```python
class Meta:
    ordering = ["name"]
    unique_together = ("name", "language_code", "language", "gender", "neural", "service", "voice_provider")
    constraints = [
        models.UniqueConstraint(
            fields=["external_id", "service", "voice_provider"],
            condition=Q(external_id__isnull=False),
            name="unique_external_id_per_service_provider",
        ),
    ]
```

The `Q` import should already be available (used in `get_for_team`). Verify and add if needed:
```python
from django.db.models import Q
```

Note: the `service` field `max_length=17` is sufficient for `"ElevenLabs"` (10 chars).

- [ ] **Step 4: Generate and apply migration**

Run: `uv run python manage.py makemigrations experiments`
Run: `uv run python manage.py migrate`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_synthetic_voice_external_id -v`
Expected: PASS

- [ ] **Step 5b: Verify migration backward compatibility**

Confirm the migration is additive (nullable field + conditional constraint) and existing data is unaffected:

Run: `uv run python manage.py showmigrations experiments | tail -5`
Expected: The new migration shows as applied.

Run: `uv run python -c "from apps.experiments.models import SyntheticVoice; print('Existing voices:', SyntheticVoice.objects.count(), '- all external_id null:', SyntheticVoice.objects.filter(external_id__isnull=True).count() == SyntheticVoice.objects.count())"`
Expected: All existing voices have `external_id=NULL`.

- [ ] **Step 6: Write test for UniqueConstraint**

Add to `apps/service_providers/tests/test_voice_providers.py`:

```python
@pytest.mark.django_db
def test_synthetic_voice_external_id_uniqueness(team_with_users):
    """Two voices with the same external_id, service, and voice_provider should be rejected"""
    from django.db import IntegrityError
    from apps.utils.factories.service_provider_factories import VoiceProviderFactory

    provider = VoiceProviderFactory(team=team_with_users)
    SyntheticVoice.objects.create(
        name="Rachel",
        external_id="21m00Tcm4TlvDq8ikWAM",
        neural=True,
        language="English",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    with pytest.raises(IntegrityError):
        SyntheticVoice.objects.create(
            name="Rachel Clone",
            external_id="21m00Tcm4TlvDq8ikWAM",
            neural=True,
            language="English",
            language_code="en",
            gender="female",
            service=SyntheticVoice.ElevenLabs,
            voice_provider=provider,
        )
```

- [ ] **Step 7: Run uniqueness test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_synthetic_voice_external_id_uniqueness -v`
Expected: PASS (constraint should already work from the migration)

- [ ] **Step 8: Lint**

Run: `uv run ruff check apps/experiments/models.py --fix && uv run ruff format apps/experiments/models.py`

- [ ] **Step 9: Commit**

```bash
git add apps/experiments/models.py apps/experiments/migrations/ apps/service_providers/tests/test_voice_providers.py
git commit -m "feat: add external_id field and ElevenLabs constant to SyntheticVoice"
```

---

### Task 3: Add `ElevenLabsSpeechService`

**Files:**
- Modify: `apps/service_providers/speech_service.py:1-271`
- Test: `apps/service_providers/tests/test_voice_providers.py`

- [ ] **Step 1: Write failing test for TTS**

Add to `apps/service_providers/tests/test_voice_providers.py`:

```python
def test_elevenlabs_voice_provider(team_with_users):
    _test_voice_provider(
        team_with_users,
        VoiceProviderType.elevenlabs,
        data={
            "elevenlabs_api_key": "test_key",
            "elevenlabs_model": "eleven_multilingual_v2",
        },
    )
```

This requires updating the `_test_voice_provider` helper's service mapping dict to include the ElevenLabs entry. Add to the `service` dict in `_test_voice_provider`:

```python
VoiceProviderType.elevenlabs: SyntheticVoice.ElevenLabs,
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_voice_provider -v`
Expected: FAIL — `VoiceProviderType` has no `elevenlabs` member.

- [ ] **Step 3: Add `ElevenLabsSpeechService` class**

Add at the end of `apps/service_providers/speech_service.py`:

```python
class ElevenLabsSpeechService(SpeechService):
    _type: ClassVar[str] = SyntheticVoice.ElevenLabs
    supports_transcription: ClassVar[bool] = True
    elevenlabs_api_key: str
    elevenlabs_model: str = "eleven_multilingual_v2"

    @property
    def _client(self):
        from elevenlabs.client import ElevenLabs as ElevenLabsClient  # noqa: PLC0415 - lazy: optional provider dep

        return ElevenLabsClient(api_key=self.elevenlabs_api_key)

    def _synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        from pydub import AudioSegment  # noqa: PLC0415 - lazy: optional audio processing lib

        audio_iter = self._client.text_to_speech.convert(
            voice_id=synthetic_voice.external_id,
            model_id=self.elevenlabs_model,
            text=text,
            output_format="mp3_44100_128",
        )
        audio_data = b"".join(audio_iter)
        audio_segment = AudioSegment.from_file(BytesIO(audio_data), format="mp3")
        duration_seconds = len(audio_segment) / 1000
        return SynthesizedAudio(audio=BytesIO(audio_data), duration=duration_seconds, format="mp3")

    def _transcribe_audio(self, audio: IO[bytes]) -> str:
        result = self._client.speech_to_text.convert(
            file=audio,
            model_id="scribe_v2",
        )
        return result.text
```

Note: The SDK import uses `ElevenLabsClient` alias to avoid shadowing the `SyntheticVoice.ElevenLabs` constant.

Note: Verify the actual SDK method names match (from Task 1 Step 3). If the SDK uses different method names (e.g., `generate` instead of `convert`, `get_all` instead of `search`), adjust accordingly.

- [ ] **Step 4: Add `elevenlabs` to `VoiceProviderType` enum**

In `apps/service_providers/models.py`, add to the `VoiceProviderType` enum:

```python
class VoiceProviderType(models.TextChoices):
    aws = "aws", _("AWS Polly")
    azure = "azure", _("Azure Text to Speech")
    openai = "openai", _("OpenAI Text to Speech")
    openai_voice_engine = "openaivoiceengine", _("OpenAI Voice Engine Text to Speech")
    elevenlabs = "elevenlabs", _("ElevenLabs")
```

Add match cases to `form_cls` property:

```python
case VoiceProviderType.elevenlabs:
    return forms.ElevenLabsVoiceConfigForm
```

Add match case to `get_speech_service()`:

```python
case VoiceProviderType.elevenlabs:
    return speech_service.ElevenLabsSpeechService(**config)
```

- [ ] **Step 5: Add config form**

In `apps/service_providers/forms.py`, add after the existing voice config forms:

```python
class ElevenLabsFileFormset(BaseFileFormSet):
    accepted_file_types = ["mp3", "mp4", "wav"]

    def clean(self) -> None:
        invalid_extensions = set()
        for _key, in_memory_file in self.files.items():
            file_extension = in_memory_file.name.rsplit(".", 1)[-1].lower()
            if file_extension not in self.accepted_file_types:
                invalid_extensions.add(f".{file_extension}")
        if invalid_extensions:
            string = ", ".join(invalid_extensions)
            raise forms.ValidationError(f"File extensions not supported: {string}")
        return super().clean()


class ElevenLabsVoiceConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["elevenlabs_api_key"]
    allow_file_upload = True
    file_formset_form = ElevenLabsFileFormset

    elevenlabs_api_key = forms.CharField(label=_("API Key"))
    elevenlabs_model = forms.ChoiceField(
        label=_("Model"),
        choices=[
            ("eleven_multilingual_v2", "Multilingual v2 (default)"),
            ("eleven_v3", "v3 (latest)"),
            ("eleven_flash_v2_5", "Flash v2.5 (low latency)"),
            ("eleven_turbo_v2_5", "Turbo v2.5"),
        ],
        initial="eleven_multilingual_v2",
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_voice_provider -v`
Expected: PASS

- [ ] **Step 7: Write TTS unit test**

Add to `apps/service_providers/tests/test_voice_providers.py`:

```python
@pytest.mark.django_db
def test_elevenlabs_synthesize_voice(team_with_users):
    """_synthesize_voice should call SDK with correct params and return SynthesizedAudio"""
    from io import BytesIO
    from apps.utils.factories.service_provider_factories import VoiceProviderFactory

    provider = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    voice = SyntheticVoice.objects.create(
        name="Rachel",
        external_id="voice_id_123",
        neural=True,
        language="English",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )

    # Create a minimal valid MP3 byte sequence for pydub to parse
    # In practice, use a small fixture file or mock AudioSegment
    fake_mp3_bytes = b"\xff\xfb\x90\x00" * 100  # minimal MP3 frame data

    speech_service = provider.get_speech_service()
    with mock.patch.object(speech_service, "_client") as mock_client:
        mock_client.text_to_speech.convert.return_value = iter([fake_mp3_bytes])
        with mock.patch("apps.service_providers.speech_service.AudioSegment") as mock_audio:
            mock_segment = mock.Mock()
            mock_segment.__len__ = mock.Mock(return_value=2500)  # 2.5 seconds in ms
            mock_audio.from_file.return_value = mock_segment

            result = speech_service._synthesize_voice("Hello world", voice)

    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="voice_id_123",
        model_id="eleven_multilingual_v2",
        text="Hello world",
        output_format="mp3_44100_128",
    )
    assert result.format == "mp3"
    assert result.duration == 2.5
    assert isinstance(result.audio, BytesIO)
```

- [ ] **Step 8: Run TTS test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_synthesize_voice -v`
Expected: PASS

- [ ] **Step 9: Write STT unit test**

```python
@pytest.mark.django_db
def test_elevenlabs_transcribe_audio(team_with_users):
    """_transcribe_audio should call SDK and return transcript text"""
    from io import BytesIO
    from apps.utils.factories.service_provider_factories import VoiceProviderFactory

    provider = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )

    speech_service = provider.get_speech_service()
    mock_audio = BytesIO(b"fake audio data")

    with mock.patch.object(speech_service, "_client") as mock_client:
        mock_result = mock.Mock()
        mock_result.text = "Hello world"
        mock_client.speech_to_text.convert.return_value = mock_result

        transcript = speech_service._transcribe_audio(mock_audio)

    assert transcript == "Hello world"
    mock_client.speech_to_text.convert.assert_called_once_with(
        file=mock_audio,
        model_id="scribe_v2",
    )
```

- [ ] **Step 10: Run STT test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_transcribe_audio -v`
Expected: PASS

- [ ] **Step 11: Write error test for missing API key (formerly Step 7)**

Add to `apps/service_providers/tests/test_voice_providers.py`:

```python
@pytest.mark.parametrize("config_key", ["elevenlabs_api_key"])
def test_elevenlabs_voice_provider_error(config_key):
    """Test that missing API key causes failure"""
    form = VoiceProviderType.elevenlabs.form_cls(
        team=None,
        data={
            "elevenlabs_api_key": "test_key",
            "elevenlabs_model": "eleven_multilingual_v2",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_voice_provider_error(VoiceProviderType.elevenlabs, data=form.cleaned_data)
```

- [ ] **Step 8: Run error test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_voice_provider_error -v`
Expected: PASS

- [ ] **Step 9: Lint**

Run: `uv run ruff check apps/service_providers/speech_service.py apps/service_providers/models.py apps/service_providers/forms.py --fix && uv run ruff format apps/service_providers/speech_service.py apps/service_providers/models.py apps/service_providers/forms.py`

- [ ] **Step 10: Run all existing voice provider tests to verify no regressions**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py -v`
Expected: All tests PASS

- [ ] **Step 11: Commit**

```bash
git add apps/service_providers/speech_service.py apps/service_providers/models.py apps/service_providers/forms.py apps/service_providers/tests/test_voice_providers.py
git commit -m "feat: add ElevenLabsSpeechService with TTS, STT, and config form"
```

---

### Task 4: Implement voice sync

**Files:**
- Modify: `apps/service_providers/models.py:300-378`
- Test: `apps/service_providers/tests/test_voice_providers.py`

- [ ] **Step 1: Write failing test for sync_voices**

Add to `apps/service_providers/tests/test_voice_providers.py`:

```python
@pytest.mark.django_db
def test_elevenlabs_sync_voices(team_with_users):
    """sync_voices should create SyntheticVoice records from ElevenLabs API response"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )

    mock_voice_1 = mock.Mock()
    mock_voice_1.voice_id = "voice_id_1"
    mock_voice_1.name = "Rachel"
    mock_voice_1.labels = {"language": "en", "gender": "female"}

    mock_voice_2 = mock.Mock()
    mock_voice_2.voice_id = "voice_id_2"
    mock_voice_2.name = "George"
    mock_voice_2.labels = {"language": "en", "gender": "male"}

    mock_response = mock.Mock()
    mock_response.voices = [mock_voice_1, mock_voice_2]
    mock_response.has_more = False

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client_cls.return_value.voices.search.return_value = mock_response
        provider.sync_voices()

    voices = provider.syntheticvoice_set.all()
    assert len(voices) == 2
    assert voices.filter(name="Rachel", external_id="voice_id_1", gender="female").exists()
    assert voices.filter(name="George", external_id="voice_id_2", gender="male").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_sync_voices -v`
Expected: FAIL — `sync_voices` method doesn't exist.

- [ ] **Step 3: Implement sync_voices**

In `apps/service_providers/models.py`, add a module-level helper for gender mapping and the `sync_voices` method to `VoiceProvider`:

At the top of the file (after existing imports), add a conditional import block:

```python
if TYPE_CHECKING:
    from elevenlabs.client import ElevenLabs as ElevenLabsClient
```

Note: `TYPE_CHECKING` may already be imported; if not, add `from typing import TYPE_CHECKING`.

Add a helper function before the `VoiceProvider` class:

```python
_ELEVENLABS_GENDER_MAP = {"male": "male", "female": "female"}


def _map_elevenlabs_gender(labels: dict) -> str:
    """Map ElevenLabs voice label gender to SyntheticVoice GENDERS choice or empty string."""
    gender = labels.get("gender", "").lower() if labels else ""
    return _ELEVENLABS_GENDER_MAP.get(gender, "")
```

Add `sync_voices` method to `VoiceProvider`:

```python
def sync_voices(self):
    """Fetch voices from ElevenLabs API and sync SyntheticVoice records for this provider."""
    if self.type != VoiceProviderType.elevenlabs:
        return

    from elevenlabs.client import ElevenLabs as ElevenLabsClient  # noqa: PLC0415

    client = ElevenLabsClient(api_key=self.config["elevenlabs_api_key"])

    # Fetch all voices (paginate if needed)
    all_voices = []
    response = client.voices.search(page_size=100)
    all_voices.extend(response.voices)
    while response.has_more:
        response = client.voices.search(page_size=100, next_page_token=response.next_page_token)
        all_voices.extend(response.voices)

    # Track which external_ids came from the API
    api_voice_ids = set()

    for voice in all_voices:
        labels = voice.labels if isinstance(voice.labels, dict) else {}
        gender = _map_elevenlabs_gender(labels)
        language = labels.get("language", "") if labels else ""

        api_voice_ids.add(voice.voice_id)

        SyntheticVoice.objects.update_or_create(
            external_id=voice.voice_id,
            service=SyntheticVoice.ElevenLabs,
            voice_provider=self,
            defaults={
                "name": voice.name,
                "neural": True,
                "language": language,
                "language_code": language,
                "gender": gender,
            },
        )

    # Remove voices no longer in API (only if not referenced by experiments)
    stale_voices = self.syntheticvoice_set.filter(
        service=SyntheticVoice.ElevenLabs,
        file__isnull=True,  # Don't touch IVC-cloned voices
    ).exclude(external_id__in=api_voice_ids)

    for voice in stale_voices:
        if not voice.experiments.exists():
            voice.delete()
```

Note: The `related_name="experiments"` on `Experiment.synthetic_voice` FK should already provide `voice.experiments`. Verify by checking the Experiment model's `synthetic_voice` field — look at the related_name. If it uses a different related_name, adjust accordingly.

- [ ] **Step 4: Run sync test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_sync_voices -v`
Expected: PASS

- [ ] **Step 5: Write test for sync update and stale voice removal**

```python
@pytest.mark.django_db
def test_elevenlabs_sync_voices_updates_and_removes(team_with_users):
    """sync_voices should update existing voices and remove stale ones not in use"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    # Pre-existing voice that will be updated
    SyntheticVoice.objects.create(
        name="Old Name",
        external_id="voice_id_1",
        neural=True,
        language="en",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    # Pre-existing voice that will be removed (not in API response)
    stale = SyntheticVoice.objects.create(
        name="Stale Voice",
        external_id="voice_id_stale",
        neural=True,
        language="en",
        language_code="en",
        gender="male",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )

    mock_voice = mock.Mock()
    mock_voice.voice_id = "voice_id_1"
    mock_voice.name = "New Name"
    mock_voice.labels = {"language": "en", "gender": "female"}

    mock_response = mock.Mock()
    mock_response.voices = [mock_voice]
    mock_response.has_more = False

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client_cls.return_value.voices.search.return_value = mock_response
        provider.sync_voices()

    voices = provider.syntheticvoice_set.all()
    assert len(voices) == 1
    assert voices.first().name == "New Name"  # Updated
    assert not SyntheticVoice.objects.filter(pk=stale.pk).exists()  # Removed
```

- [ ] **Step 6: Run update/remove test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_sync_voices_updates_and_removes -v`
Expected: PASS

- [ ] **Step 7: Write test for gender mapping**

```python
@pytest.mark.parametrize(
    ("labels", "expected_gender"),
    [
        ({"gender": "male"}, "male"),
        ({"gender": "Female"}, "female"),
        ({"gender": "non-binary"}, ""),
        ({}, ""),
        (None, ""),
    ],
)
def test_elevenlabs_gender_mapping(labels, expected_gender):
    from apps.service_providers.models import _map_elevenlabs_gender
    assert _map_elevenlabs_gender(labels) == expected_gender
```

- [ ] **Step 8: Run gender mapping test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_gender_mapping -v`
Expected: PASS

- [ ] **Step 9: Lint**

Run: `uv run ruff check apps/service_providers/models.py apps/service_providers/tests/test_voice_providers.py --fix && uv run ruff format apps/service_providers/models.py apps/service_providers/tests/test_voice_providers.py`

- [ ] **Step 10: Run all voice provider tests**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py -v`
Expected: All PASS

- [ ] **Step 11: Commit**

```bash
git add apps/service_providers/models.py apps/service_providers/tests/test_voice_providers.py
git commit -m "feat: add voice sync for ElevenLabs provider"
```

---

### Task 5: Implement IVC file upload and provider delete

**Files:**
- Modify: `apps/service_providers/models.py:300-378`
- Test: `apps/service_providers/tests/test_voice_providers.py`

- [ ] **Step 1: Write failing test for add_files (IVC)**

```python
@pytest.mark.django_db
def test_elevenlabs_add_files_ivc(team_with_users):
    """add_files should upload to ElevenLabs API and create SyntheticVoice with external_id"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    files = FileFactory.create_batch(2, name=factory.Sequence(lambda n: f"voice_{n}.mp3"))

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client_cls.return_value.voices.ivc.create.side_effect = [
            mock.Mock(voice_id="cloned_id_1"),
            mock.Mock(voice_id="cloned_id_2"),
        ]
        provider.add_files(files)

    voices = provider.syntheticvoice_set.all()
    assert len(voices) == 2
    assert voices.filter(external_id="cloned_id_1").exists()
    assert voices.filter(external_id="cloned_id_2").exists()
    for voice in voices:
        assert voice.service == SyntheticVoice.ElevenLabs
        assert voice.file is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_add_files_ivc -v`
Expected: FAIL — `add_files` doesn't handle `elevenlabs` type.

- [ ] **Step 3: Update add_files for ElevenLabs**

In `apps/service_providers/models.py`, update `VoiceProvider.add_files()`:

```python
@transaction.atomic()
def add_files(self, files):
    if self.type == VoiceProviderType.openai_voice_engine:
        for file in files:
            try:
                SyntheticVoice.objects.create(
                    name=file.name,
                    neural=True,
                    language="",
                    language_code="",
                    gender="",
                    service=SyntheticVoice.OpenAIVoiceEngine,
                    voice_provider=self,
                    file=file,
                )
            except IntegrityError:
                message = f"Unable to upload '{file.name}' voice. This voice might already exist"
                raise ValidationError(message) from None
    elif self.type == VoiceProviderType.elevenlabs:
        from elevenlabs.client import ElevenLabs as ElevenLabsClient  # noqa: PLC0415

        client = ElevenLabsClient(api_key=self.config["elevenlabs_api_key"])
        for file in files:
            try:
                file_content = file.file.read()
                file.file.seek(0)
                response = client.voices.ivc.create(
                    name=file.name,
                    files=[file_content],
                )
                SyntheticVoice.objects.create(
                    name=file.name,
                    external_id=response.voice_id,
                    neural=True,
                    language="",
                    language_code="",
                    gender="",
                    service=SyntheticVoice.ElevenLabs,
                    voice_provider=self,
                    file=file,
                )
            except IntegrityError:
                message = f"Unable to upload '{file.name}' voice. This voice might already exist"
                raise ValidationError(message) from None
```

Note: The `file.file` access depends on how the `File` model exposes its content. Check `apps/files/models.py` to see how to read file bytes from a `File` instance. It may be `file.file.read()`, `file.content`, or require opening via storage backend. Adjust accordingly.

- [ ] **Step 4: Run IVC test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_add_files_ivc -v`
Expected: PASS

- [ ] **Step 5: Write failing test for delete**

```python
@pytest.mark.django_db
def test_elevenlabs_provider_delete(team_with_users):
    """Deleting ElevenLabs provider should attempt API cleanup for cloned voices and delete local records"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    # A synced catalog voice (no file)
    catalog_voice = SyntheticVoice.objects.create(
        name="Rachel",
        external_id="voice_id_1",
        neural=True,
        language="en",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    # A cloned voice (has file)
    cloned_file = FileFactory.create(name="clone.mp3")
    cloned_voice = SyntheticVoice.objects.create(
        name="My Clone",
        external_id="cloned_id_1",
        neural=True,
        language="",
        language_code="",
        gender="",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
        file=cloned_file,
    )

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        provider.delete()
        # Should only attempt to delete the cloned voice from API
        mock_client.voices.delete.assert_called_once_with(voice_id="cloned_id_1")

    # Both local records should be gone
    assert not SyntheticVoice.objects.filter(pk=catalog_voice.pk).exists()
    assert not SyntheticVoice.objects.filter(pk=cloned_voice.pk).exists()
```

- [ ] **Step 6: Update delete for ElevenLabs**

In `apps/service_providers/models.py`, update `VoiceProvider.delete()`:

```python
@transaction.atomic()
def delete(self):  # ty: ignore[invalid-method-override]
    if self.type == VoiceProviderType.openai_voice_engine:
        files_to_delete = self.get_files()
        [f.delete() for f in files_to_delete]
    elif self.type == VoiceProviderType.elevenlabs:
        from elevenlabs.client import ElevenLabs as ElevenLabsClient  # noqa: PLC0415

        client = ElevenLabsClient(api_key=self.config["elevenlabs_api_key"])
        # Delete only IVC-cloned voices from ElevenLabs API
        cloned_voices = self.syntheticvoice_set.filter(file__isnull=False)
        for voice in cloned_voices:
            try:
                client.voices.delete(voice_id=voice.external_id)
            except Exception:
                log.warning("Failed to delete ElevenLabs voice %s from API", voice.external_id)
        # Delete local files
        files_to_delete = self.get_files()
        [f.delete() for f in files_to_delete]
    return super().delete()
```

Ensure a logger is set up at the top of `models.py`. Check if one already exists; if not, add:
```python
import logging

log = logging.getLogger("ocs.service_providers")
```

This must be done before implementing the `delete` method that uses `log.warning`.

- [ ] **Step 7: Run delete test**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py::test_elevenlabs_provider_delete -v`
Expected: PASS

- [ ] **Step 8: Lint**

Run: `uv run ruff check apps/service_providers/models.py apps/service_providers/tests/test_voice_providers.py --fix && uv run ruff format apps/service_providers/models.py apps/service_providers/tests/test_voice_providers.py`

- [ ] **Step 9: Run all voice provider tests**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add apps/service_providers/models.py apps/service_providers/tests/test_voice_providers.py
git commit -m "feat: add ElevenLabs IVC file upload and provider delete cleanup"
```

---

### Task 6: Add sync voices endpoint and trigger on provider create

**Files:**
- Modify: `apps/service_providers/views.py`
- Modify: `apps/service_providers/urls.py`
- Test: `apps/service_providers/tests/test_views.py`

- [ ] **Step 1: Add sync endpoint URL**

In `apps/service_providers/urls.py`, add:

```python
path("<slug:provider_type>/<int:pk>/sync-voices/", views.sync_voices, name="sync_voices"),
```

- [ ] **Step 2: Add sync_voices view**

In `apps/service_providers/views.py`, add a function-based view:

```python
@require_POST
@login_and_team_required
@permission_required("service_providers.change_voiceprovider", raise_exception=True)
def sync_voices(request, team_slug: str, provider_type: str, pk: int):
    provider = get_object_or_404(VoiceProvider, team=request.team, pk=pk)
    provider.sync_voices()
    messages.success(request, "Voices synced successfully.")
    return redirect("single_team:manage_team", team_slug=team_slug)
```

Check existing imports in `views.py` — `require_POST` and `login_and_team_required` are already imported. Add these missing imports:

```python
from django.contrib import messages
from django.shortcuts import redirect
from apps.service_providers.models import VoiceProvider
```

- [ ] **Step 3: Trigger sync on provider create**

In `apps/service_providers/views.py`, modify `CreateServiceProvider.form_valid()` to call `sync_voices()` after saving an ElevenLabs provider:

```python
@transaction.atomic()
def form_valid(self, form, file_formset):
    instance = form.save()
    instance.team = self.request.team
    instance.save()
    if file_formset:
        files = file_formset.save(self.request)
        instance.add_files(files)
    # Sync voices for providers that support it
    if hasattr(instance, 'sync_voices'):
        instance.sync_voices()
```

Or more explicitly, guard with a type check since `CreateServiceProvider` handles all provider types (LLM, Messaging, etc.), not just Voice:

```python
    if isinstance(instance, VoiceProvider) and instance.type == VoiceProviderType.elevenlabs.value:
        instance.sync_voices()
```

Use `isinstance` to avoid crashing when saving non-voice providers.

- [ ] **Step 4: Lint**

Run: `uv run ruff check apps/service_providers/views.py apps/service_providers/urls.py --fix && uv run ruff format apps/service_providers/views.py apps/service_providers/urls.py`

- [ ] **Step 5: Write test for sync_voices endpoint**

Add to `apps/service_providers/tests/test_views.py`:

```python
@pytest.mark.django_db
def test_sync_voices_endpoint(team_with_users):
    """POST to sync-voices endpoint should call sync_voices on the provider"""
    from apps.service_providers.models import VoiceProvider, VoiceProviderType

    team = team_with_users
    provider = VoiceProvider.objects.create(
        team=team,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    client = Client()
    client.force_login(team.members.first())
    url = reverse("service_providers:sync_voices", kwargs={
        "team_slug": team.slug,
        "provider_type": "voice",
        "pk": provider.pk,
    })
    with mock.patch.object(VoiceProvider, "sync_voices") as mock_sync:
        response = client.post(url)

    assert response.status_code == 302
    mock_sync.assert_called_once()
```

Check existing test imports in `test_views.py` — add `Client`, `reverse`, and `mock` if not present.

- [ ] **Step 6: Run all view tests**

Run: `uv run pytest apps/service_providers/tests/test_views.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add apps/service_providers/views.py apps/service_providers/urls.py apps/service_providers/tests/test_views.py
git commit -m "feat: add sync voices endpoint and auto-sync on ElevenLabs provider create"
```

---

### Task 7: Final integration verification

**Files:**
- All modified files

- [ ] **Step 1: Run all voice provider tests**

Run: `uv run pytest apps/service_providers/tests/test_voice_providers.py -v`
Expected: All PASS

- [ ] **Step 2: Run all service provider tests**

Run: `uv run pytest apps/service_providers/tests/ -v`
Expected: All PASS

- [ ] **Step 3: Lint all modified files**

Run: `uv run ruff check apps/service_providers/ apps/experiments/models.py --fix && uv run ruff format apps/service_providers/ apps/experiments/models.py`

- [ ] **Step 4: Type check**

Run: `uv run ty check apps/service_providers/ apps/experiments/models.py`
Expected: No new errors

- [ ] **Step 5: Run broader test suite for regressions**

Run: `uv run pytest apps/experiments/tests/ -v --timeout=60`
Expected: All PASS (no regressions from SyntheticVoice model changes)

- [ ] **Step 6: Commit any final fixes**

If any lint/type fixes were needed:

```bash
git add -u
git commit -m "chore: lint and type fixes for ElevenLabs integration"
```
