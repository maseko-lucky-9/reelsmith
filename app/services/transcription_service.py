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

    log.info("Speech to text via Google  audio=%s  language=%s", audio_path, language)
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(audio_path) as source:
            audio_data = recognizer.record(source)
        result = recognizer.recognize_google(audio_data, language=language)
        log.info("Transcription complete  words=%d", len(result.split()) if result else 0)
        return result
    except sr.UnknownValueError:
        log.warning("Speech recognition could not understand the audio  audio=%s", audio_path)
        return ""
    except sr.RequestError as e:
        log.error("Speech recognition request failed  audio=%s  error=%s", audio_path, e)
        return ""
