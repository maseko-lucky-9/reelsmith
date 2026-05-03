import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.ollama_service import generate_social_content

_DEFAULTS = dict(base_url="http://localhost:11434", model="mistral", timeout=30)


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"response": json.dumps(payload)}
    resp.raise_for_status.return_value = None
    return resp


@patch("app.services.ollama_service.httpx.post")
def test_returns_description_and_hashtags(mock_post):
    mock_post.return_value = _mock_response(
        {"description": "Great clip", "hashtags": ["#python", "#code"]}
    )

    desc, tags = generate_social_content("Title", "transcript text", **_DEFAULTS)

    assert desc == "Great clip"
    assert tags == ["#python", "#code"]


@patch("app.services.ollama_service.httpx.post")
def test_returns_empty_on_http_error(mock_post):
    mock_post.return_value.raise_for_status.side_effect = Exception("500")

    desc, tags = generate_social_content("Title", "transcript", **_DEFAULTS)

    assert desc == ""
    assert tags == []


@patch("app.services.ollama_service.httpx.post")
def test_returns_empty_on_invalid_json(mock_post):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"response": "not json {{{{"}
    mock_post.return_value = resp

    desc, tags = generate_social_content("Title", "transcript", **_DEFAULTS)

    assert desc == ""
    assert tags == []


@patch("app.services.ollama_service.httpx.post")
def test_returns_empty_on_connection_error(mock_post):
    mock_post.side_effect = ConnectionError("refused")

    desc, tags = generate_social_content("Title", "transcript", **_DEFAULTS)

    assert desc == ""
    assert tags == []


@patch("app.services.ollama_service.httpx.post")
def test_truncates_transcript_to_2000_chars(mock_post):
    mock_post.return_value = _mock_response({"description": "ok", "hashtags": []})
    long_transcript = "x" * 5000

    generate_social_content("Title", long_transcript, **_DEFAULTS)

    call_body = mock_post.call_args[1]["json"]
    assert len(call_body["prompt"]) < 5000 + 500


@patch("app.services.ollama_service.httpx.post")
def test_missing_keys_in_response_return_defaults(mock_post):
    mock_post.return_value = _mock_response({})

    desc, tags = generate_social_content("Title", "transcript", **_DEFAULTS)

    assert desc == ""
    assert tags == []
