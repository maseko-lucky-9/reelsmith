"""Root conftest — applied to every test in the project.

1. Applies PIL.Image.ANTIALIAS compat shim before any test can import MoviePy.
2. Suppresses SpeechRecognition's aifc/audioop DeprecationWarnings at import
   time (before pytest's filterwarnings machinery is active).
"""
import warnings

# SpeechRecognition imports aifc + audioop, both removed in Python 3.13.
# Must be suppressed here (not just pyproject.toml) because the warnings fire
# during module collection before pytest activates its filterwarnings config.
warnings.filterwarnings("ignore", message="'aifc' is deprecated", category=DeprecationWarning)
warnings.filterwarnings("ignore", message="'audioop' is deprecated", category=DeprecationWarning)

import app.compat  # noqa: F401 — PIL.Image.ANTIALIAS shim for MoviePy 1.0.3
