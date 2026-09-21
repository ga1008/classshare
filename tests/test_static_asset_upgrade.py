"""Exercise the pre-cutover Compose command gate without Docker or user data."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required for the deployment command-gate test")
class StaticAssetUpgradeTests(unittest.TestCase):
    def run_gate(self, case):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            wrapper = directory / "mock_commands.sh"
            log = directory / "commands.log"
            stream = directory / "received.tar"
            wrapper.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
fake_compose() {
  printf 'compose %s\n' "$*" >> "$MOCK_LOG"
  case "$*" in
    '--project-name fixture ps -a -q app')
      case "$MOCK_CASE" in
        fresh) return 0 ;;
        ambiguous) printf 'old-app\nother-app\n' ;;
        discovery-failure) return 7 ;;
        *) printf 'old-app\n' ;;
      esac ;;
    '--project-name fixture run --rm --no-deps -T --entrypoint python app tools/publish_static_assets.py --seed-vite-tar -')
      cat > "$MOCK_STREAM"
      if [ "$MOCK_CASE" = 'validation-failure' ]; then return 9; fi ;;
    *) return 99 ;;
  esac
}
docker() {
  printf 'docker %s\n' "$*" >> "$MOCK_LOG"
  [ "$*" = 'cp old-app:/app/static/dist/assets/. -' ] || return 98
  if [ "$MOCK_CASE" = 'export-failure' ]; then return 8; fi
  printf 'mocked-archive'
}
export -f fake_compose docker
bash "$1" fake_compose --project-name fixture
''', encoding="utf-8", newline="\n")
            environment = {
                **os.environ, "MOCK_CASE": case, "MOCK_LOG": log.as_posix(),
                "MOCK_STREAM": stream.as_posix(),
            }
            result = subprocess.run(
                [BASH, wrapper.as_posix(), (ROOT / "deployment/docker/seed_previous_static_assets.sh").as_posix()],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20,
            )
            return result, log.read_text() if log.exists() else "", stream.read_bytes() if stream.exists() else None

    def test_previous_container_exports_before_replacement_with_compose_options(self):
        result, commands, received = self.run_gate("success")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"mocked-archive", received)
        self.assertIn("compose --project-name fixture ps -a -q app", commands)
        self.assertIn("docker cp old-app:/app/static/dist/assets/. -", commands)
        self.assertIn("run --rm --no-deps -T --entrypoint python app tools/publish_static_assets.py --seed-vite-tar -", commands)
        self.assertNotIn(" stop ", commands)
        self.assertNotIn(" up ", commands)

    def test_fresh_install_skips_only_when_no_old_container_exists(self):
        result, commands, received = self.run_gate("fresh")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("docker cp", commands)
        self.assertIsNone(received)

    def test_ambiguous_old_container_is_a_hard_gate(self):
        result, commands, _ = self.run_gate("ambiguous")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("docker cp", commands)

    def test_failed_container_discovery_is_not_mistaken_for_fresh_install(self):
        result, commands, _ = self.run_gate("discovery-failure")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("docker cp", commands)

    def test_export_failure_is_not_hidden_by_successful_consumer(self):
        result, _, _ = self.run_gate("export-failure")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("refusing application cutover", result.stderr)

    def test_publisher_validation_failure_is_a_hard_gate(self):
        result, _, _ = self.run_gate("validation-failure")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("refusing application cutover", result.stderr)


if __name__ == "__main__":
    unittest.main()
