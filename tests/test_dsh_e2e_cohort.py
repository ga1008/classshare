"""Cohort planning only; no Docker, server, production key or model requests."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import dsh_isolated_e2e as harness, dsh_isolated_e2e_app as app
from tools.dsh_e2e_cohort import Cohort, names, profile_digest, verify_inputs


def cohort(name='checkpoint-b'):
    return Cohort(name,18002,'sha256:'+'1'*64,'sha256:'+'2'*64,'a'*40,'b'*64,'c'*64,'d'*64,99)


class DshE2eCohortTests(unittest.TestCase):
    def args(self, item):
        return ['dsh_isolated_e2e.py','prepare','--dry-run','--cohort',item.name,'--port',str(item.port),
                '--app-image',item.app_image,'--dsh-image',item.dsh_image,'--source-commit',item.source_commit,
                '--archive-sha256',item.archive_sha256,'--manifest-sha256',item.manifest_sha256,
                '--profile-sha256',item.profile_sha256,'--expected-key-id',str(item.expected_key_id)]

    def test_names_isolate_every_mutable_resource_and_keep_old_cohort(self):
        first, second = names('checkpoint-b'), names('checkpoint-c')
        for field in first:
            self.assertNotEqual(first[field],second[field])
        self.assertTrue(first['root'].endswith('/e2e-checkpoint-b'))
        self.assertNotEqual(first['root'],cohort().plan()['old_e2e_path_preserved'])
        for invalid in ('e2e','legacy','../old','two_words','/absolute','name?x','x'*25):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                names(invalid)

    def test_ports_and_digest_pins_are_required(self):
        for values in ({'port':18001},{'port':8000},{'app_image':'latest'},{'source_commit':'HEAD'},
                       {'profile_sha256':''},{'expected_key_id':0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(cohort(),**values)

    def test_actual_cli_dry_run_makes_no_external_call_or_filesystem_mutation(self):
        out=io.StringIO()
        with patch('sys.argv',self.args(cohort())), redirect_stdout(out), \
             patch.object(harness.subprocess,'run',side_effect=AssertionError('No commands in dry-run')), \
             patch.object(harness.subprocess,'Popen',side_effect=AssertionError('No processes in dry-run')), \
             patch.object(harness.Path,'mkdir',side_effect=AssertionError('No writes in dry-run')):
            harness.main()
        result=json.loads(out.getvalue())
        self.assertTrue(result['dry_run'])
        self.assertEqual(0,result['production_reads'])
        self.assertEqual(0,result['model_requests'])
        self.assertEqual('127.0.0.1:18002:8000',result['bind'])

    def test_app_guard_rejects_legacy_or_other_database_without_importing_app(self):
        derived=names('checkpoint-b')
        env={'DSH_E2E_COHORT':'checkpoint-b','DATABASE_URL':f"postgresql://e2e_app:synthetic@{derived['pg']}:5432/{derived['database']}",
             'MAIN_DATA_DIR':'/e2e-data'}
        with patch.dict('os.environ',env,clear=True):
            app.guard()
        for url in ('postgresql://e2e_app:x@lanshare-dsh-e2e-pg/lanshare_dsh_e2e',
                    f"postgresql://postgres:x@{derived['pg']}/{derived['database']}",
                    f"postgresql://e2e_app:x@{derived['pg']}/production"):
            with patch.dict('os.environ',{**env,'DATABASE_URL':url},clear=True), self.assertRaises(RuntimeError):
                app.guard()

    def make_inputs(self, root, *, duplicate=False, omitted=False):
        profile=root/'profile';profile.mkdir()
        (profile/'package.json').write_text('{}\n')
        (profile/'cordis.patch.yml').write_text('[]\n')
        files={name:b'# Synthetic freeze fixture only\n' for name in ('tools/dsh_isolated_e2e.py','tools/dsh_isolated_e2e_app.py','tools/dsh_e2e_cohort.py','tools/agent_dsh_launcher.py')}
        with tarfile.open(root/'source.tar.gz','w:gz') as archive:
            for index,(name,content) in enumerate(files.items()):
                if omitted and index==3: continue
                member=tarfile.TarInfo(name);member.size=len(content)
                archive.addfile(member,io.BytesIO(content))
                if duplicate and index==0: archive.addfile(member,io.BytesIO(content))
        archive_hash=hashlib.sha256((root/'source.tar.gz').read_bytes()).hexdigest()
        manifest={'source_commit':'a'*40,'archive_sha256':archive_hash,'files':{name:hashlib.sha256(content).hexdigest() for name,content in files.items()},'removed':[]}
        manifest_path=root/'source.manifest.json';manifest_path.write_text(json.dumps(manifest))
        return replace(cohort(),archive_sha256=archive_hash,manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),profile_sha256=profile_digest(profile))

    def test_local_preflight_checks_frozen_archive_manifest_and_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);item=self.make_inputs(root)
            self.assertEqual(4,len(verify_inputs(item,root)['files']))
            out=io.StringIO()
            with patch('sys.argv',self.args(item)+['--check-inputs',str(root)]),redirect_stdout(out):
                harness.main()
            self.assertEqual(4,json.loads(out.getvalue())['verified_input_files'])
            (root/'profile/cordis.patch.yml').write_text('changed')
            with self.assertRaisesRegex(ValueError,'profile digest'):
                verify_inputs(item,root)

    def test_duplicate_and_omitted_archive_members_are_rejected(self):
        for options in ({'duplicate':True},{'omitted':True}):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);item=self.make_inputs(root,**options)
                with self.assertRaises(ValueError):
                    verify_inputs(item,root)

    def test_paid_phase_without_explicit_flag_fails_before_host_or_key_access(self):
        arguments=[value for value in self.args(cohort()) if value!='--dry-run']
        arguments[1]='teacher'
        with (patch('sys.argv',arguments),patch.object(harness.subprocess,'run',side_effect=AssertionError('No process')),
              redirect_stderr(io.StringIO()),self.assertRaises(SystemExit)):
            harness.main()
