"""Pure naming and immutable-input checks for a NEW isolated E2E cohort."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

LAB = PurePosixPath('/lanshare/.codex-temp/dsh-migration-20260910')


def names(cohort):
    if not isinstance(cohort, str) or not re.fullmatch(r'[a-z][a-z0-9-]{2,23}', cohort) or cohort in {'e2e', 'legacy'}:
        raise ValueError('Use a new cohort name: 3..24 lowercase letters/digits/hyphens')
    return {'root': str(LAB / ('e2e-' + cohort)), 'inputs': str(LAB / 'e2e-inputs' / cohort),
            'pg': 'lanshare-dsh-e2e-' + cohort + '-pg', 'app': 'lanshare-dsh-e2e-' + cohort + '-app',
            'copy': 'lanshare-dsh-e2e-' + cohort + '-copy', 'network': 'lanshare_dsh_e2e_' + cohort.replace('-', '_'),
            'database': 'lanshare_dsh_e2e_' + cohort.replace('-', '_')}


@dataclass(frozen=True)
class Cohort:
    name: str
    port: int
    app_image: str
    dsh_image: str
    source_commit: str
    archive_sha256: str
    manifest_sha256: str
    profile_sha256: str
    expected_key_id: int

    def __post_init__(self):
        names(self.name)
        if type(self.port) is not int or not 18002 <= self.port <= 18999:
            raise ValueError('New E2E port must be 18002..18999; old E2E and production ports are excluded')
        for value in (self.app_image, self.dsh_image):
            if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
                raise ValueError('Immutable Docker image sha256 is required')
        for value, length in ((self.source_commit, 40), (self.archive_sha256, 64), (self.manifest_sha256, 64), (self.profile_sha256, 64)):
            if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', value):
                raise ValueError('Exact frozen source/manifest/archive/profile pins are required')
        if type(self.expected_key_id) is not int or self.expected_key_id <= 0:
            raise ValueError('Explicit expected active service key ID is required')

    def plan(self):
        return {**asdict(self), **names(self.name), 'bind': f'127.0.0.1:{self.port}:8000',
                'production_database_writes': False, 'production_user_sessions': False,
                'old_e2e_path_preserved': str(LAB / 'e2e'), 'paid_phases_require_explicit_flag': ['teacher', 'student', 'admin']}


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def profile_digest(root):
    digest = hashlib.sha256()
    for name in ('package.json', 'cordis.patch.yml'):
        digest.update(name.encode() + b'\0')
        digest.update((root / name).read_bytes().replace(b'\r\n', b'\n') + b'\0')
    return digest.hexdigest()


def relative_file(name):
    if not isinstance(name, str) or '\\' in name or '\x00' in name or ':' in name:
        raise ValueError('Invalid source manifest path')
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(part in {'.', '..'} for part in path.parts):
        raise ValueError('Source paths must be canonical relative files')
    return path


def verify_inputs(cohort, root=None):
    root = Path(root if root is not None else names(cohort.name)['inputs'])
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Input cohort directory is missing or redirected')
    for name in ('source.tar.gz', 'source.manifest.json', 'profile'):
        if (root / name).is_symlink():
            raise ValueError('Input cohort symlink is forbidden')
    for name in ('source.tar.gz', 'source.manifest.json', 'profile/package.json', 'profile/cordis.patch.yml'):
        if not (root / name).is_file() or (root / name).is_symlink():
            raise ValueError('Input cohort requires regular immutable files')
    if {path.name for path in (root / 'profile').iterdir()} != {'package.json', 'cordis.patch.yml'}:
        raise ValueError('Only the two immutable profile inputs may be supplied')
    if sha256(root / 'source.tar.gz') != cohort.archive_sha256 or sha256(root / 'source.manifest.json') != cohort.manifest_sha256:
        raise ValueError('Frozen archive or manifest digest differs')
    if profile_digest(root / 'profile') != cohort.profile_sha256:
        raise ValueError('Frozen profile digest differs')
    manifest = json.loads((root / 'source.manifest.json').read_text())
    if manifest.get('source_commit') != cohort.source_commit or manifest.get('archive_sha256') != cohort.archive_sha256:
        raise ValueError('Manifest source identity differs')
    files, removed = manifest.get('files'), manifest.get('removed', [])
    if not isinstance(files, dict) or not 1 <= len(files) <= 20000 or not isinstance(removed, list) or len(removed) > 2000:
        raise ValueError('Manifest file set is invalid')
    for name, digest in files.items():
        relative_file(name)
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('Invalid source file digest')
    for name in removed:
        relative_file(name)
        if name in files:
            raise ValueError('Manifest cannot retain and remove the same file')
    required = {'tools/dsh_isolated_e2e.py', 'tools/dsh_isolated_e2e_app.py', 'tools/dsh_e2e_cohort.py', 'tools/agent_dsh_launcher.py'}
    if not required.issubset(files):
        raise ValueError('Frozen archive must include the exact cohort harness and launcher')
    seen, total = set(), 0
    with tarfile.open(root / 'source.tar.gz', 'r|gz') as archive:
        for member in archive:
            if not member.isfile() or member.name not in files or member.name in seen or not 0 <= member.size <= 64 * 1024 * 1024:
                raise ValueError('Archive entry is not one bounded frozen regular file')
            seen.add(member.name)
            total += member.size
            if total > 512 * 1024 * 1024:
                raise ValueError('Archive exceeds the isolated source limit')
            with archive.extractfile(member) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != files[member.name]:
                    raise ValueError('Archive file content differs from manifest')
    if seen != set(files):
        raise ValueError('Archive does not contain the complete frozen file set')
    return manifest
