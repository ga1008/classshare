"""Linux native import syscall checks from exact source, with synthetic files only."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import uuid

from agent_platform_read_safety_probe import HTTPException, function


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--upload-source',type=Path,required=True)
    parser.add_argument('--download-source',type=Path,required=True)
    args=parser.parse_args()
    if os.name!='posix':
        raise SystemExit('This probe requires Linux')
    with tempfile.TemporaryDirectory(prefix='lanshare-download-probe-') as temporary:
        fixture=Path(temporary)
        fixture.chmod(0o755)
        storage=fixture/'storage';storage.mkdir()
        tasks=fixture/'tasks';tasks.mkdir()
        outside=fixture/'outside';outside.mkdir()
        secret=outside/'private.bin';secret.write_bytes(b'outside synthetic private data')
        namespace={'os':os,'Path':Path,'PurePosixPath':PurePosixPath,'stat':stat,
            'hashlib':hashlib,'tempfile':tempfile,'contextmanager':contextmanager,'re':re,'uuid':uuid,
            'HTTPException':HTTPException,'MAX_DOWNLOAD_BYTES':4096,'MAX_IMPORT_BYTES':8192,'MAX_IMPORT_ENTRIES':4,
            'allowed_file_roots':lambda:[storage,tasks]}
        namespace['_open_confined']=function(args.upload_source,'_open_confined',namespace)
        for name in ('source_snapshot','_directory_descriptor','_existing_import','_assert_import_path',
                     '_assert_import_inode','_remove_own_import','publish_snapshot'):
            namespace[name]=function(args.download_source,name,namespace)
        snapshot=namespace['source_snapshot']
        def publish(*args, authorize=lambda:None):
            return namespace['publish_snapshot'](*args, authorize=authorize)
        data=b'\x00\xffsynthetic binary source\x01'*20
        source=storage/'source.bin';source.write_bytes(data)
        checks={}

        def task(name):
            root=tasks/name;root.mkdir()
            return root

        def denied(call,code):
            try:call()
            except HTTPException as exc:return exc.status_code==code
            return False

        root=task('normal')
        with snapshot(source) as value:
            path=publish(root,value,'book.xlsx')
            checks['binary_bytes_and_sha_match']=(root/path).read_bytes()==data and value['sha256']==hashlib.sha256(data).hexdigest()
            checks['new_input_has_no_write_mode_bits']=stat.S_IMODE((root/path).stat().st_mode)==0o444
            checks['repeat_deduplicates_same_bytes']=publish(root,value,'book.xlsx')==path and len(list((root/'inputs').iterdir()))==1
            changed=root/path;changed.unlink();changed.write_bytes(b'x'*len(data))
            checks['existing_changed_input_not_overwritten']=denied(lambda:publish(root,value,'book.xlsx'),409) and changed.read_bytes()==b'x'*len(data)

        def runner_can_read(path):
            result=subprocess.run([sys.executable,'-c',
                'import pathlib,sys; pathlib.Path(sys.argv[1]).read_bytes()',str(path)],
                user=10001,group=10001,extra_groups=[],capture_output=True,timeout=5)
            return result.returncode==0

        root=task('disclosure_boundary')
        with snapshot(source) as value:
            observed=[]
            def authorize():
                staged=list((root/'inputs').iterdir())
                observed.append(bool(staged) and all(stat.S_IMODE(path.stat().st_mode)==0o600
                    and not runner_can_read(path) for path in staged))
            path=publish(root,value,'private.xlsx',authorize=authorize)
            checks['actual_runner_uid_cannot_read_before_authorization']=observed==[True]
            checks['actual_runner_uid_can_read_after_authorization']=runner_can_read(root/path)
            def reject():raise HTTPException(401,'synthetic revoked authority')
            checks['denied_replay_keeps_previously_disclosed_input']=denied(
                lambda:publish(root,value,'private.xlsx',authorize=reject),401) and (root/path).read_bytes()==data

        root=task('denied_disclosure')
        with snapshot(source) as value:
            checks['denied_authorization_removes_both_new_names']=denied(
                lambda:publish(root,value,'private.xlsx',authorize=reject),401) and not list((root/'inputs').iterdir())

        root=task('runner_precreated_inputs')
        (root/'inputs').mkdir();os.chown(root/'inputs',10001,10001)
        with snapshot(source) as value:
            path=publish(root,value,'private.xlsx')
            attempted=subprocess.run([sys.executable,'-c',
                'import pathlib,sys; p=pathlib.Path(sys.argv[1]);p.unlink();p.write_bytes(b"replacement")',str(root/path)],
                user=10001,group=10001,extra_groups=[],capture_output=True,timeout=5)
            checks['precreated_inputs_becomes_trusted_managed_directory']=(root/'inputs').stat().st_uid==os.geteuid()
            checks['runner_cannot_swap_import_between_inode_check_and_cleanup']=attempted.returncode!=0 and (root/path).read_bytes()==data

        root=task('replacement_before_disclosure')
        with snapshot(source) as value:
            name=value['sha256']+'-private.xlsx'
            def replace_destination():
                target=root/'inputs'/name
                target.unlink();target.write_bytes(b'unrelated runner-owned content')
            checks['replacement_inode_is_neither_disclosed_nor_cleaned']=denied(
                lambda:publish(root,value,'private.xlsx',authorize=replace_destination),409) and (
                    root/'inputs'/name).read_bytes()==b'unrelated runner-owned content' and len(list((root/'inputs').iterdir()))==1

        source_link=storage/'redirect.bin';source_link.symlink_to(secret)
        def read_link():
            with snapshot(source_link):pass
        checks['source_symlink_rejected']=denied(read_link,403)

        root=task('redirect_inputs');(root/'inputs').symlink_to(outside,target_is_directory=True)
        with snapshot(source) as value:
            checks['inputs_symlink_rejected_without_outside_write']=denied(lambda:publish(root,value,'book.xlsx'),403) and list(outside.iterdir())==[secret]

        link_root=tasks/'redirect_task';link_root.symlink_to(outside,target_is_directory=True)
        with snapshot(source) as value:
            checks['task_directory_symlink_rejected']=denied(lambda:publish(link_root,value,'book.xlsx'),403) and list(outside.iterdir())==[secret]

        root=task('target_link')
        with snapshot(source) as value:
            path=publish(root,value,'book.xlsx');(root/path).unlink();(root/path).symlink_to(secret)
            checks['destination_symlink_not_followed']=denied(lambda:publish(root,value,'book.xlsx'),403) and secret.read_bytes()==b'outside synthetic private data'
            (root/path).unlink();os.mkfifo(root/path,0o600)
            checks['destination_fifo_does_not_block']=denied(lambda:publish(root,value,'book.xlsx'),409)

        large=storage/'large.bin';large.write_bytes(b'x'*4097)
        def read_large():
            with snapshot(large):pass
        checks['source_size_bound']=denied(read_large,413)

        root=task('entry_quota')
        with snapshot(source) as value:
            publish(root,value,'one.bin')
            namespace['MAX_IMPORT_ENTRIES']=1
            checks['entry_quota_preserves_existing_input']=denied(lambda:publish(root,value,'two.bin'),413) and len(list((root/'inputs').iterdir()))==1
            namespace['MAX_IMPORT_ENTRIES']=4
            namespace['MAX_IMPORT_BYTES']=len(data)
            checks['byte_quota_preserves_existing_input']=denied(lambda:publish(root,value,'two.bin'),413) and len(list((root/'inputs').iterdir()))==1
            namespace['MAX_IMPORT_BYTES']=8192

        root=task('renamed_inputs')
        original_directory=namespace['_directory_descriptor']
        def rename_after_open(target):
            descriptor=original_directory(target)
            (target/'inputs').rename(target/'original-inputs')
            (target/'inputs').mkdir()
            return descriptor
        namespace['_directory_descriptor']=rename_after_open
        with snapshot(source) as value:
            checks['renamed_output_directory_yields_no_false_receipt']=denied(lambda:publish(root,value,'book.xlsx'),403) and not list((root/'original-inputs').iterdir())
        namespace['_directory_descriptor']=original_directory

        root=task('source_swapped')
        with snapshot(source) as value:
            source.rename(storage/'old-source.bin');source.symlink_to(secret)
            path=publish(root,value,'book.xlsx')
            checks['source_swap_after_snapshot_keeps_original_bytes']=(root/path).read_bytes()==data

        report={'kind':'exact_source_linux_download_publication_probe','checks':checks,
            'source_sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.upload_source,args.download_source)},
            'fixture_bounds':{'single_file':4096,'inputs_bytes':8192,'inputs_entries':4},
            'production_data_accessed':False,'paid_model_calls':0,'full_authenticated_http_integration':False,
            'passed':all(checks.values())}
        print(json.dumps(report,indent=2))
        if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
