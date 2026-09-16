"""Shared fixtures for voice module tests."""

import io

import pytest


class AudioFile(io.BytesIO):
    """In-memory audio upload with Streamlit-compatible metadata."""

    name = "recording.wav"
    type = "audio/wav"


@pytest.fixture
def mock_audio_file():
    """BytesIO mock audio file for STT tests."""
    return AudioFile(b"fake audio bytes")
