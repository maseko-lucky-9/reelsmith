"""Dump the FastAPI OpenAPI schema to web/src/api/openapi.json.

Usage:
    python -m app.scripts.dump_openapi
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    # Import after path setup so the app module resolves correctly.
    from app.main import app

    schema = app.openapi()
    out = Path(__file__).parents[2] / "web" / "src" / "api" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2))
    print(f"OpenAPI schema written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
