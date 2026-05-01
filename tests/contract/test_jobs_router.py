from fastapi.testclient import TestClient

from app.main import create_app


def test_post_job_returns_accepted_and_state_persists(tmp_path):
    with TestClient(create_app()) as client:
        response = client.post(
            "/jobs",
            json={"url": "https://www.youtube.com/watch?v=xxxxxxxxxxx", "download_path": str(tmp_path)},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted"
        job_id = body["job_id"]

        get_response = client.get(f"/jobs/{job_id}")
    assert get_response.status_code == 200
    state = get_response.json()
    assert state["job_id"] == job_id
    assert state["url"].endswith("xxxxxxxxxxx")
    # Status is volatile (orchestrator runs in background); assert it's a known value.
    assert state["status"] in {"pending", "running", "failed", "completed"}


def test_get_unknown_job_returns_404():
    with TestClient(create_app()) as client:
        response = client.get("/jobs/does-not-exist")
    assert response.status_code == 404
