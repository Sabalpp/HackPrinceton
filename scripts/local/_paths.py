"""Bootstrap local tools without installing collector dependencies into the API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/collector"))

from farcare_collector.paths import (  # noqa: E402
    COLLECTOR_ROOT,
    REPO_ROOT,
    WORKSPACE_ROOT,
)

__all__ = ["COLLECTOR_ROOT", "REPO_ROOT", "WORKSPACE_ROOT"]
