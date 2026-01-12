# Speech Service Integration Testing

This guide covers integration tests for speech services that make real API calls to verify the implementation.

## Setup

### 1. Create Integration Environment File

Copy the example file and add your real API credentials:

```bash
cp apps/service_providers/tests/.env.integration.example apps/service_providers/tests/.env.integration
```

Edit `.env.integration` and add your credentials:

```bash
# OpenAI
OPENAI_API_KEY=sk-proj-...

# AWS
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1

# Azure
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=eastus
```

**Note:** `.env.integration` is in `.gitignore` and will not be committed.

### 2. Get API Credentials

- **OpenAI**: https://platform.openai.com/api-keys
- **AWS**: https://console.aws.amazon.com/iam/
- **Azure**: https://portal.azure.com/ (Cognitive Services → Speech)

## Running Integration Tests

### Run All Integration Tests

```bash
pytest apps/service_providers/tests/test_speech_integration.py -v -s
```

### Run Specific Service Tests

```bash
# OpenAI only
pytest apps/service_providers/tests/test_speech_integration.py::TestOpenAISpeechIntegration -v -s

# AWS only
pytest apps/service_providers/tests/test_speech_integration.py::TestAWSSpeechIntegration -v -s

# Azure only
pytest apps/service_providers/tests/test_speech_integration.py::TestAzureSpeechIntegration -v -s
```

### Check Credentials Status

```bash
pytest apps/service_providers/tests/test_speech_integration.py::test_credentials_status -v -s
```

### Skip Integration Tests (Default Behavior)

```bash
# Run all tests EXCEPT integration tests
pytest -m "not speech_integration"
```

## Using Management Command for Quick Testing

The management command is useful for quick manual testing during development:

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

# Use different env file
python manage.py test_speech_live --service openai --env-file .env.prod
```

## Test Coverage

### OpenAI Tests
- ✓ Speech synthesis with real API
- ✓ Transcription (error handling)
- ✓ Verify correct model name (`gpt-4o-mini-transcribe`)

### AWS Tests
- ✓ Speech synthesis with Polly
- ✓ Neural voice support

### Azure Tests
- ✓ Speech synthesis with Cognitive Services
- ✓ Transcription (error handling)
- ✓ WAV format output

## Notes

- Tests automatically skip if credentials are not configured
- Tests use `django-environ` to load credentials securely
- `.env.integration` takes precedence over `.env` if present
- All tests are marked with `@pytest.mark.speech_integration`
- Audio output is validated (format, duration, size)

## CI/CD Integration

To run integration tests in CI, set environment variables:

```yaml
# GitHub Actions example
- name: Run integration tests
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
    AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
    AZURE_SPEECH_KEY: ${{ secrets.AZURE_SPEECH_KEY }}
  run: pytest apps/service_providers/tests/test_speech_integration.py -v
```

## Troubleshooting

### Tests Skip with "Credentials not set"

Ensure your `.env.integration` file exists and contains valid credentials for the service you're testing.

### Import Errors

Make sure you have all required dependencies installed:

```bash
uv pip install pydub boto3 azure-cognitiveservices-speech openai
```

### Audio Processing Errors

If you see pydub errors, ensure ffmpeg is installed:

```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```
