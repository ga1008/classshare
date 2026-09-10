"""Deployment cutover constraints without changing host files or services."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dsh_installer', REPO / 'deployment/dsh/install_launcher.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class DshDeploymentTests(unittest.TestCase):
    def test_replaces_legacy_controls_and_preserves_other_credentials(self):
        source = ('# retained\nDB_PASSWORD=synthetic-db\nDEEPSEEK_API_KEY=synthetic-other-ai\n'
                  'AGENT_TASK_RUNTIME_TOKEN=synthetic-legacy\nDEEPSEEK_RUNTIME_TOKEN=legacy\n'
                  'AGENT_TASK_RUNTIME_MODEL=deepseek-v4-pro\nAGENT_TASK_GLOBAL_CONCURRENCY=2\n'
                  'AGENT_TASK_WORKER_CONCURRENCY=2\nAGENT_DSH_ENABLED=false\n')
        result = installer.environment_text(source)
        self.assertIn('DB_PASSWORD=synthetic-db\n', result)
        self.assertIn('DEEPSEEK_API_KEY=synthetic-other-ai\n', result)
        self.assertNotIn('synthetic-legacy', result)
        self.assertNotIn('AGENT_TASK_RUNTIME_', result)
        self.assertIn('AGENT_MODEL_DEFAULT=deepseek-v4-pro\n', result)
        self.assertIn('AGENT_DSH_ENABLED=true\n', result)
        self.assertIn('AGENT_TASK_GLOBAL_CONCURRENCY=1\n', result)
        self.assertIn('AGENT_TASK_WORKER_CONCURRENCY=1\n', result)
        self.assertIn('AGENT_MODEL_SEARCH_MODEL=deepseek-flash\n', result)
        self.assertEqual(installer.environment_text(result), result)

    def test_profile_drift_is_rejected_before_host_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'deployment/dsh/profile'
            profile.mkdir(parents=True)
            (profile / 'package.json').write_text('{}\n')
            (profile / 'cordis.patch.yml').write_text('[]\n')
            release = root / 'deployment/dsh/release.json'
            manifest = {'dsh_package_version': '0.1.5-rc.1', 'image': 'sha256:' + 'a' * 64,
                        'profile_sha256': installer.profile_digest(profile)}
            release.write_text(json.dumps(manifest))
            self.assertEqual(installer.manifest_at(root), manifest)
            (profile / 'cordis.patch.yml').write_text('changed\n')
            with self.assertRaisesRegex(ValueError, 'differs'):
                installer.manifest_at(root)

    def test_unit_has_narrow_control_and_no_provider_key_environment(self):
        unit = installer.unit_text(Path('/lanshare'), {'image': 'sha256:' + 'a' * 64})
        self.assertIn('--max-concurrency 1', unit)
        self.assertIn('ProtectSystem=strict', unit)
        self.assertNotIn('EnvironmentFile', unit)
        self.assertNotIn('docker.env', unit)
        self.assertIn('RestrictAddressFamilies=AF_UNIX AF_INET', unit)

    @unittest.skipUnless((REPO / 'deployment/deploy_remote.ps1').is_file(), 'Local operator deployment script is not part of a source checkout')
    def test_local_operator_deploy_phases_preserve_migration_before_activation(self):
        script = (REPO / 'deployment/deploy_remote.ps1').read_text(encoding='utf-8-sig')
        # The real production script is a local operator file; core functions
        # and installer remain tracked even where this file is gitignored.
        self.assertLess(script.index('dsh_preflight "$remote_path"'), script.index('run_quiesced_migration()'))
        self.assertLess(script.index('  run_quiesced_migration\n'), script.index('dsh_activate "$remote_path"'))
        self.assertLess(script.index('dsh_activate "$remote_path"'), script.index('up -d --no-build --pull never'))
        self.assertIn('dsh_verify_and_retire "$remote_path"', script)
        self.assertNotIn('DEEPSEEK_TUI_', script)
