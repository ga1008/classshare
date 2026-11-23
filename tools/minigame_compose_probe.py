"""Verify the merged game deployment model without connecting to a Docker daemon."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "docker-compose.yml",
    "docker-compose.postgres.yml",
    "docker-compose.minigames.yml",
)


def inspect(compose):
    scratch = ROOT / ".codex-temp/scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=scratch, prefix="minigame-compose-") as owned:
        work = Path(owned)
        for name in FILES:
            shutil.copyfile(ROOT / name, work / name)
        (work / "docker.env").write_text(
            "DB_ENGINE=postgres\nSECRET_KEY=fixture\n", encoding="utf-8"
        )
        (work / "minigames.env").write_text(
            "MINIGAMES_APP_ORIGIN=https://class.example.invalid\n"
            "MINIGAMES_CONTENT_ORIGIN=https://games.example.invalid\n"
            "MINIGAMES_SIGNING_SECRET=fixture\n"
            "MINIGAMES_DATABASE_URL=postgresql://fixture@postgres/fixture\n"
            "MINIGAMES_VHOST_CONF=./fixture.conf\nMINIGAMES_TLS_DIR=./tls\nMINIGAMES_REGISTER_DIR=./register\n",
            encoding="utf-8",
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "PATHEXT",
                "TEMP",
                "TMP",
                "HOME",
                "USERPROFILE",
            }
        }
        env["DOCKER_CONFIG"] = str(work / "docker-client")
        args = [*compose, "--env-file", "minigames.env"]
        for name in FILES:
            args.extend(["-f", name])
        result = subprocess.run(
            [*args, "config", "--format", "json"],
            cwd=work,
            env=env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        services = json.loads(result.stdout)["services"]
        api, edge, content = (
            services[name] for name in ("game-api", "nginx", "game-content")
        )
        report = {
            "trustedApiWorkers": api["command"][-2:] == ["--workers", "2"],
            "gameReadiness": all(
                service["healthcheck"]["test"] == ["CMD", "python", "/app/deployment/docker/minigame_health.py", kind]
                for service, kind in ((api, "api"), (content, "content"))
            ),
            "databaseSlotsPerProcess": all(
                str(services[name]["environment"]["GAMES_DB_WORKERS"]) == "1"
                for name in ("app", "game-api", "game-content")
            ),
            "classroomExternalApi": str(
                services["app"]["environment"]["MINIGAMES_EXTERNAL_API"]
            )
            == "1",
            "preferClassroomCpu": int(api["cpu_shares"])
            < int(services["app"]["cpu_shares"]),
            "isolatedContentMount": any(
                v["target"].endswith("/games/versions") and v.get("read_only")
                for v in content["volumes"]
            ),
            "optionalApiProxy": any(
                v["target"].endswith("/game-api.conf") and v.get("read_only")
                for v in edge["volumes"]
            ),
            "independentClassroomStart": "app" in edge["depends_on"]
            and not {"game-api", "game-content"}.intersection(edge["depends_on"]),
            "internalGamePorts": not api.get("ports") and not content.get("ports"),
            "separateDeleteRegister": any(
                v["target"] == "/app/minigame-register" for v in api["volumes"]
            ),
        }
        failed = [k for k, ok in report.items() if not ok]
        if failed:
            raise ValueError("Compose checks failed: " + ", ".join(failed))
        return {"checks": report, "ranContainers": False, "businessDataUsed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compose",
        type=Path,
        help="Standalone Compose executable; defaults to docker compose",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            inspect(
                [str(args.compose.resolve())] if args.compose else ["docker", "compose"]
            ),
            indent=2,
        )
    )
