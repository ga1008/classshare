from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_EXPECTED_ENGINES = {"sqlite", "postgres"}
DATABASE_URL_RE = re.compile(
    r"\bpostgres(?:ql)?://(?P<userinfo>[^@\s]+)@(?P<host>[^/\s]+)",
    re.IGNORECASE,
)
DATABASE_URL_USERINFO_RE = re.compile(r"\bpostgres(?:ql)?://[^@\s]+@", re.IGNORECASE)


def _status_from_failures(failures: list[dict[str, str]]) -> str:
    return "failed" if failures else "ok"


def _as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else str(value).strip().lower() == "true"


def _database_backend(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    backend = payload.get("database_backend")
    if isinstance(backend, Mapping):
        return backend
    return None


def _redact_database_url_userinfo(value: str) -> str:
    return DATABASE_URL_USERINFO_RE.sub("postgresql://***:***@", value)


def _contains_plaintext_database_url(value: str) -> bool:
    for match in DATABASE_URL_RE.finditer(value):
        userinfo = match.group("userinfo")
        if userinfo != "***:***":
            return True
    return False


def build_health_backend_report(
    payload: Mapping[str, Any],
    *,
    expected_engine: str | None = None,
    require_configured: bool = True,
    forbid_plaintext_database_url: bool = True,
) -> dict[str, Any]:
    """Validate the database backend section from /api/internal/health."""

    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    normalized_expected = (expected_engine or "").strip().lower() or None
    if normalized_expected and normalized_expected not in SUPPORTED_EXPECTED_ENGINES:
        raise ValueError(
            f"expected_engine must be one of {sorted(SUPPORTED_EXPECTED_ENGINES)}, got {expected_engine!r}"
        )

    backend = _database_backend(payload)
    observed_engine = ""
    observed_configured = False
    observed_details = ""
    if backend is None:
        failures.append(
            {
                "id": "PGH-R001",
                "message": "/api/internal/health did not include database_backend.",
            }
        )
    else:
        observed_engine = str(backend.get("engine") or "").strip().lower()
        observed_configured = _as_bool(backend.get("configured"))
        observed_details = str(backend.get("details") or "")

    if normalized_expected and observed_engine and observed_engine != normalized_expected:
        failures.append(
            {
                "id": "PGH-R002",
                "message": f"Expected database backend '{normalized_expected}', got '{observed_engine}'.",
            }
        )

    if require_configured and backend is not None and not observed_configured:
        failures.append(
            {
                "id": "PGH-R003",
                "message": "Database backend is not configured according to health payload.",
            }
        )

    contains_plaintext_url = _contains_plaintext_database_url(observed_details)

    if forbid_plaintext_database_url and contains_plaintext_url:
        failures.append(
            {
                "id": "PGH-R004",
                "message": "Health payload appears to expose a plaintext PostgreSQL connection URL.",
            }
        )

    if observed_engine == "sqlite" and normalized_expected == "postgres":
        warnings.append(
            {
                "id": "PGH-W001",
                "message": "Application is still reporting SQLite during a PostgreSQL verification step.",
            }
        )

    return {
        "status": _status_from_failures(failures),
        "expected_engine": normalized_expected,
        "observed": {
            "engine": observed_engine,
            "configured": observed_configured,
            "details": _redact_database_url_userinfo(observed_details),
        },
        "failures": failures,
        "warnings": warnings,
        "safety": {
            "production_data_modified": False,
            "remote_data_modified": False,
            "contains_plaintext_database_url": contains_plaintext_url,
        },
    }


def _load_payload(path: Path | None) -> Mapping[str, Any]:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("health payload must be a JSON object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate /api/internal/health database backend state."
    )
    parser.add_argument("--input", type=Path, help="Path to a saved health JSON payload.")
    parser.add_argument("--output", type=Path, help="Write the validation report to this path.")
    parser.add_argument(
        "--expected-engine",
        choices=sorted(SUPPORTED_EXPECTED_ENGINES),
        help="Expected database backend engine.",
    )
    parser.add_argument(
        "--allow-unconfigured",
        action="store_true",
        help="Do not fail when database_backend.configured is false.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    report = build_health_backend_report(
        _load_payload(args.input),
        expected_engine=args.expected_engine,
        require_configured=not args.allow_unconfigured,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
