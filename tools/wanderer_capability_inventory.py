"""Source-only inventory; isolate configuration before loading mounted routes."""
from contextlib import ExitStack
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main():
    from tools.isolated_environment import isolate_sqlite_environment, guard_dotenv_loading, guard_postgres_connections
    with tempfile.TemporaryDirectory(prefix="lanshare-capabilities-") as folder, ExitStack() as guards:
        runtime = Path(folder)
        (runtime / "db").mkdir()
        isolate_sqlite_environment(runtime)
        guard_dotenv_loading(guards)
        guard_postgres_connections(guards)
        from classroom_app.app import app
        from classroom_app.services.wanderer_platform_service import platform_capability_matrix
        rows = platform_capability_matrix(app)
        output = {"schema_version": 1, "generated_from": "mounted_normal_platform_routes",
                  "scope": "service transport availability, not permission grants; every object is authorized by its normal route",
                  "counts": dict(Counter(row["status"] for row in rows)), "routes": rows}
        target = REPO / "docs" / "wanderer-platform-capabilities-2026-11-09.json"
        target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(target), "counts": output["counts"]}))


if __name__ == "__main__":
    main()
