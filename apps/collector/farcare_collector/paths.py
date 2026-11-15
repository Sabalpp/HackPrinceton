"""Keep private workspace files separate from relocated source and SQL assets."""

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
COLLECTOR_ROOT = PACKAGE_ROOT.parent
# Source/editable checkout, or the caller's directory for an installed wheel.
REPO_ROOT = next(
    (parent for parent in PACKAGE_ROOT.parents if (parent / "apps/api/app/main.py").is_file()),
    Path.cwd(),
)
# Preserve the original Mac workspace layout. A normal standalone checkout uses
# its own root; FARCARE_WORKSPACE can point at an existing private data directory.
WORKSPACE_ROOT = (
    Path(
        os.environ.get(
            "FARCARE_WORKSPACE",
            str(REPO_ROOT.parent if REPO_ROOT.name == "app_repo" else REPO_ROOT),
        )
    )
    .expanduser()
    .resolve()
)
SQL_ROOT = Path(__file__).resolve().parent / "integrations/sql"
