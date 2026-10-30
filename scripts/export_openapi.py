"""Export the actual FastAPI contract without connecting to a database."""

import json
from pathlib import Path

from trustrepro.main import app


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "var" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(destination)
