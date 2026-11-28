"""Guard: every table defined in app/*/models.py must be registered via app.models.

Alembic autogenerate only sees tables present in Base.metadata. A model missing
from app/models.py would make it propose DROP TABLE for live data.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _declared_tables() -> dict[str, Path]:
    tables: dict[str, Path] = {}
    for path in APP_DIR.glob("*/models.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets
            ):
                if isinstance(node.value, ast.Constant):
                    tables[node.value.value] = path
    return tables


def test_every_declared_table_is_in_base_metadata():
    declared = _declared_tables()
    assert declared, "no models found; glob is wrong"
    # Fresh interpreter: importing only app.models, so models pulled in by other
    # tests (or routers) in this process can't mask a missing registration.
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys, json; sys.path.insert(0, sys.argv[1]);"
         "import app.models; from app.core.database import Base;"
         "print(json.dumps(sorted(Base.metadata.tables)))",
         str(APP_DIR.parent)],
        capture_output=True, text=True, check=True,
    ).stdout
    registered = set(json.loads(out.strip().splitlines()[-1]))
    missing = {t: str(p.relative_to(APP_DIR.parent)) for t, p in declared.items() if t not in registered}
    assert not missing, f"Add these models to app/models.py: {missing}"
