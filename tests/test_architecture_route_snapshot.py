import json
import unittest
from pathlib import Path

from fastapi.routing import APIRoute

from classroom_app.app import app


ROUTE_SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "p02_route_snapshot.json"


def _current_route_snapshot() -> list[dict]:
    snapshot = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            snapshot.append(
                {
                    "path": route.path,
                    "methods": sorted(route.methods or []),
                    "name": route.name,
                    "include_in_schema": bool(route.include_in_schema),
                }
            )
    return sorted(
        snapshot,
        key=lambda item: (item["path"], ",".join(item["methods"]), item["name"]),
    )


class ArchitectureRouteSnapshotTests(unittest.TestCase):
    def test_fastapi_route_contract_matches_reviewed_baseline(self):
        expected = json.loads(ROUTE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        actual = _current_route_snapshot()

        self.assertEqual(
            expected,
            actual,
            "Route changes require review of path/method/name/include_in_schema and a baseline update.",
        )


if __name__ == "__main__":
    unittest.main()
