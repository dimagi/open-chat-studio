# Integration Testing

Integration tests make real API calls to external services to verify the complete implementation. All integration tests share the same `.env.integration` configuration file.

## Setup

### Create Integration Environment File

Copy the example file and add your real API credentials:

```bash
cp .env.integration.example .env.integration
```

Edit `.env.integration` and add credentials for the services you want to test. See `.env.integration.example` for all available options and links to get API keys.

**Note:** `.env.integration` is in `.gitignore` and will not be committed.

## Running Tests

### Run All Integration Tests

```bash
pytest -m integration -v -s
```

### Skip Integration Tests (Default)

```bash
# Run all tests EXCEPT integration tests
pytest -m "not integration"
```

## Speech Service Integration Tests

**Test file:** `apps/service_providers/tests/test_speech_integration.py`

### What the Tests Cover

- **OpenAI**: Text-to-speech (synthesis) and speech-to-text (transcription)
- **AWS Polly**: Text-to-speech synthesis
- **Azure Cognitive Services**: Text-to-speech and speech-to-text

### Running Speech Tests

```bash
pytest apps/service_providers/tests/test_speech_integration.py -m integration -v -s
```

### Management Command for Quick Testing

Useful for manual testing during development:

```bash
# Test all services
python manage.py test_speech_live --service all

# Test specific service
python manage.py test_speech_live --service openai
python manage.py test_speech_live --service aws
python manage.py test_speech_live --service azure

# Custom text
python manage.py test_speech_live --service openai --text "Hello world"

# Save audio files
python manage.py test_speech_live --service all --save-audio /tmp/test_audio
```

## LLM Provider Integration Tests

**Test file:** `apps/service_providers/tests/test_llm_integration.py`

### Running LLM Tests

```bash
pytest apps/service_providers/tests/test_llm_integration.py -m integration -v -s
```

## Troubleshooting

### Tests Skip with "Credentials not set"

Tests automatically skip if required credentials are not found in `.env.integration`. Add the missing credentials to enable those tests.

### Audio Processing Errors (Speech Tests)

If you see pydub errors, ensure ffmpeg is installed:

```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```
