# Integration Testing

This guide covers integration tests for services that make real API calls to verify the implementation.

## Setup

### 1. Create Integration Environment File

Copy the example file and add your real API credentials:

```bash
cp .env.integration.example .env.integration
```

Edit `.env.integration` and add your credentials.

**Note:** `.env.integration` is in `.gitignore` and will not be committed.

## Running Integration Tests

### Run All Integration Tests

```bash
pytest -m integration -v -s
```

### Skip Integration Tests (Default Behaviour)

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

## CI/CD Integration

To run integration tests in CI, set environment variables:

```yaml
# GitHub Actions example
- name: Run integration tests
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
    AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
    AWS_REGION: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
    AZURE_SPEECH_KEY: ${{ secrets.AZURE_SPEECH_KEY }}
  run: pytest -m integration -v
```

## Troubleshooting

### Tests Skip with "Credentials not set"

Ensure your `.env.integration` file exists and contains valid credentials for the service you're testing.

### Audio Processing Errors

If you see pydub errors, ensure ffmpeg is installed:

```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```
