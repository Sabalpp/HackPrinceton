"""Export shared contracts to packages/contracts/ (JSON Schema + OpenAPI).

Run from backend:  uv run python scripts/export_contracts.py
CI runs this and fails if the committed files differ (contract drift check).
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("BACKGROUND_TASKS_ENABLED", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.schemas.contracts import EXPORTED_CONTRACTS  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "packages" / "contracts"


def dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main() -> None:
    for name, model in EXPORTED_CONTRACTS.items():
        dump(OUT / "schemas" / f"{name}.schema.json", model.model_json_schema())
    dump(OUT / "openapi.json", app.openapi())
    print(f"Exported {len(EXPORTED_CONTRACTS)} schemas + openapi.json to {OUT}")


if __name__ == "__main__":
    main()
