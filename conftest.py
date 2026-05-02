"""Root conftest — applied to every test in the project.

Applies PIL.Image.ANTIALIAS compat shim before any test can import MoviePy.
"""
import app.compat  # noqa: F401 — PIL.Image.ANTIALIAS shim for MoviePy 1.0.3
