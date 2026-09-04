import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.deploy.dependency_images import (
    LABEL_PREFIX, RECIPES, REPO_ROOT, ensure_images, image_plan, verify_labels, write_context,
)


class DependencyImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in set(sum((list(paths) for paths in RECIPES.values()), [])):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((REPO_ROOT / name).read_bytes())

    def test_code_changes_do_not_invalidate_dependencies(self):
        before = image_plan(self.root)
        (self.root / 'app.py').write_text('print("new feature")')
        self.assertEqual(before, image_plan(self.root))

    def test_lock_change_only_invalidates_its_own_image(self):
        before = image_plan(self.root)
        lock = self.root / 'requirements.lock.txt'
        lock.write_bytes(lock.read_bytes() + b'\n# dependency change\n')
        after = image_plan(self.root)
        self.assertNotEqual(before['runtime']['image'], after['runtime']['image'])
        self.assertEqual(before['frontend']['image'], after['frontend']['image'])

    def test_recipe_change_invalidates_image(self):
        before = image_plan(self.root)
        recipe = self.root / 'deployment/docker/Dockerfile.frontend-deps'
        recipe.write_bytes(recipe.read_bytes() + b'\n# base update\n')
        self.assertNotEqual(before['frontend']['image'], image_plan(self.root)['frontend']['image'])

    def test_windows_and_linux_line_endings_use_same_images(self):
        before = image_plan(self.root)
        for path in self.root.rglob('*'):
            if path.is_file():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        self.assertEqual(before, image_plan(self.root))

    def test_build_context_is_a_small_allowlist(self):
        (self.root / 'docker.env').write_text('PRIVATE_SECRET=must-not-enter-image')
        spec = image_plan(self.root)['runtime']
        with tempfile.TemporaryDirectory() as output:
            destination = Path(output)
            write_context(self.root, destination, spec)
            names = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
            self.assertEqual(set(spec['inputs']) | {'dependency-inputs.sha256'}, names)

    def test_wrong_metadata_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'refusing to reuse'):
            verify_labels(image_plan(self.root)['runtime'], {})

    @patch('tools.deploy.dependency_images.subprocess.run')
    @patch('tools.deploy.dependency_images.inspect_labels')
    def test_existing_images_do_not_run_builds(self, inspect, run):
        specs = image_plan(self.root)
        inspect.side_effect = [{LABEL_PREFIX + 'fingerprint': s['fingerprint'], LABEL_PREFIX + 'kind': s['kind']} for s in specs.values()]
        ensure_images(self.root)
        self.assertFalse(any('build' in call.args[0] for call in run.call_args_list))
        self.assertEqual(2, sum(call.args[0][:3] == ['docker', 'image', 'tag'] for call in run.call_args_list))

    @patch('tools.deploy.dependency_images.subprocess.run')
    @patch('tools.deploy.dependency_images.inspect_labels')
    def test_failure_does_not_promote_current_aliases(self, inspect, run):
        spec = image_plan(self.root)['runtime']
        inspect.side_effect = [{LABEL_PREFIX + 'fingerprint': spec['fingerprint'], LABEL_PREFIX + 'kind': 'runtime'}, {}]
        with self.assertRaises(RuntimeError):
            ensure_images(self.root)
        self.assertFalse(any(call.args[0][:3] == ['docker', 'image', 'tag'] for call in run.call_args_list))


if __name__ == '__main__':
    unittest.main()
