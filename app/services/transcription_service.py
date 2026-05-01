import logging

import speech_recognition as sr

from app.settings import settings

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def speech_to_text(audio_path: str, language: str = "en-US") -> str:
    provider = settings.transcription_provider
    if provider == "stub":
        log.info("Stub transcription provider; returning placeholder text")
        return "stub transcription text for testing"

    log.info("Speech to text via Google: %s", audio_path)
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(audio_path) as source:
            audio_data = recognizer.record(source)
        return recognizer.recognize_google(audio_data, language=language)
    except sr.UnknownValueError:
        log.warning("Speech recognition could not understand the audio")
        return ""
    except sr.RequestError as e:
        log.error("Speech recognition request failed: %s", e)
        return ""
