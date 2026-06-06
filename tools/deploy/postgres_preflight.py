from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPOSE_FILE = REPO_ROOT / "docker-compose.postgres.yml"
DEFAULT_ENV_EXAMPLE = REPO_ROOT / "docker.env.example"
DEFAULT_ENV_FILE = REPO_ROOT / "docker.env"
DEFAULT_DEPLOY_SCRIPT = REPO_ROOT / "deployment" / "deploy_remote.ps1"
SECRET_PLACEHOLDER_RE = re.compile(r"(change[-_ ]?me|replace|password-not-for-production|secret)", re.I)
SENSITIVE_ENV_KEY_RE = re.compile(
    r"(PASSWORD|PASSWD|PWD|SECRET|TOKEN|API[_-]?KEY|DATABASE_URL|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|CREDENTIAL|USERNAME)",
    re.I,
)


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"required file not found: {path}")
    return path.read_text(encoding="utf-8")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _contains_all(text: str, required: tuple[str, ...]) -> list[str]:
    return [item for item in required if item not in text]


def _redact_env(values: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in values.items():
        if SENSITIVE_ENV_KEY_RE.search(key):
            redacted[key] = "***" if value else ""
        else:
            redacted[key] = value
    return redacted


def check_postgres_preflight(
    *,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
    env_example: Path = DEFAULT_ENV_EXAMPLE,
    env_file: Path = DEFAULT_ENV_FILE,
    deploy_script: Path = DEFAULT_DEPLOY_SCRIPT,
    migration_report: Path | None = None,
) -> dict[str, Any]:
    compose_text = _read_text(compose_file)
    env_example_text = _read_text(env_example)
    deploy_text = _read_text(deploy_script)
    env_values = parse_env_file(env_file)

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    compose_missing = _contains_all(
        compose_text,
        (
            "postgres:",
            "postgres:16-alpine",
            "pg_isready",
            "/var/lib/postgresql/data",
            "./data/postgres",
            "condition: service_healthy",
        ),
    )
    if compose_missing:
        blockers.append(
            {
                "id": "PGD-R001",
                "message": "PostgreSQL compose overlay is missing required service, healthcheck, or volume markers.",
                "details": ", ".join(compose_missing),
            }
        )

    env_missing = _contains_all(
        env_example_text,
        (
            "DB_ENGINE=sqlite",
            "DATABASE_URL=",
            "POSTGRES_BACKEND_READY=false",
            "POSTGRES_IMAGE=postgres:16-alpine",
            "POSTGRES_HOST=postgres",
            "POSTGRES_PASSWORD=",
            "POSTGRES_HOST_DATA_DIR=./data/postgres",
        ),
    )
    if env_missing:
        blockers.append(
            {
                "id": "PGD-R002",
                "message": "docker.env.example is missing PostgreSQL deployment variables.",
                "details": ", ".join(env_missing),
            }
        )

    deploy_missing = _contains_all(
        deploy_text,
        (
            "data/",
            "docker.env",
            "SkipDatabaseBackup",
            "compose_cmd",
            "config --quiet",
        ),
    )
    if deploy_missing:
        blockers.append(
            {
                "id": "PGD-R003",
                "message": "deploy script is missing runtime-data protection or dry-run markers.",
                "details": ", ".join(deploy_missing),
            }
        )

    if "ports:" in compose_text:
        warnings.append(
            {
                "id": "PGD-W001",
                "message": "PostgreSQL overlay should normally stay on the internal Compose network without public ports.",
            }
        )

    configured_engine = env_values.get("DB_ENGINE", "sqlite").strip().lower() or "sqlite"
    postgres_cutover_requested = configured_engine == "postgres"
    if postgres_cutover_requested:
        required_env = ("DATABASE_URL", "POSTGRES_PASSWORD")
        missing_env = [key for key in required_env if not env_values.get(key)]
        if missing_env:
            blockers.append(
                {
                    "id": "PGD-R004",
                    "message": "DB_ENGINE=postgres requires explicit database URL and password in the real docker.env.",
                    "details": ", ".join(missing_env),
                }
            )
        if env_values.get("POSTGRES_BACKEND_READY", "").lower() != "true":
            blockers.append(
                {
                    "id": "PGD-R005",
                    "message": "DB_ENGINE=postgres requires POSTGRES_BACKEND_READY=true after migration gates pass.",
                    "details": "POSTGRES_BACKEND_READY is not true.",
                }
            )
        password = env_values.get("POSTGRES_PASSWORD", "")
        if SECRET_PLACEHOLDER_RE.search(password):
            blockers.append(
                {
                    "id": "PGD-R006",
                    "message": "PostgreSQL password still looks like a placeholder.",
                    "details": "Replace it in remote docker.env; never commit it.",
                }
            )
        if migration_report is None or not migration_report.is_file():
            blockers.append(
                {
                    "id": "PGD-R007",
                    "message": "PostgreSQL cutover requires a migration validation report artifact.",
                    "details": str(migration_report or ""),
                }
            )
    else:
        warnings.append(
            {
                "id": "PGD-W002",
                "message": "docker.env is not requesting PostgreSQL cutover; deployment remains in SQLite mode.",
            }
        )

    return {
        "status": "failed" if blockers else "ok",
        "compose_file": str(compose_file),
        "env_example": str(env_example),
        "env_file": str(env_file),
        "env_file_exists": env_file.is_file(),
        "deploy_script": str(deploy_script),
        "migration_report": str(migration_report) if migration_report else "",
        "postgres_cutover_requested": postgres_cutover_requested,
        "production_data_modified": False,
        "remote_data_modified": False,
        "checked_env": _redact_env(env_values),
        "blockers": blockers,
        "warnings": warnings,
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"postgres preflight report written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check PostgreSQL Docker Compose deployment gates.")
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--env-example", type=Path, default=DEFAULT_ENV_EXAMPLE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--deploy-script", type=Path, default=DEFAULT_DEPLOY_SCRIPT)
    parser.add_argument("--migration-report", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = check_postgres_preflight(
            compose_file=args.compose_file,
            env_example=args.env_example,
            env_file=args.env_file,
            deploy_script=args.deploy_script,
            migration_report=args.migration_report,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "production_data_modified": False,
            "remote_data_modified": False,
        }

    _write_report(report, args.json_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
