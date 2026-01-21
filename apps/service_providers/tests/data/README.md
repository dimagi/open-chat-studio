# Test Data for Speech Service Integration Tests

This directory contains test audio files used by the speech service integration tests.

## Files

### speech_sample1.mp3

- **Format**: MP3 (48kHz stereo, 320kbps)
- **Duration**: ~9.9 seconds
- **Content**: Female voice reading the text:
  > "Oh, I do feel so ill all over me, my dear Ribby; I have swallowed a large tin patty-pan with a sharp scalloped edge!"
- **Usage**: Used for testing transcription services (OpenAI, Azure)
- **Source**: Public domain audio from LibriVox

## Purpose

These files are used by integration tests to verify that speech services correctly:
- Transcribe audio to text
- Handle various audio formats
- Return accurate transcription results

## Adding New Test Files

When adding new test audio files:
1. Use public domain or properly licensed audio
2. Keep files small (<1MB preferred)
3. Document the content and expected transcription
4. Update this README
