from __future__ import annotations

import uuid


def new_job_id() -> str:
    return uuid.uuid4().hex


def new_event_id() -> str:
    return uuid.uuid4().hex
