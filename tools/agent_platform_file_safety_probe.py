"""Linux syscall component probe using the exact workspace upload helpers.

No app initialization, databases, network or model calls. Extracting the two
functions avoids importing application configuration into an isolated fixture.
This proves filesystem helper behavior, not full authenticated upload routing.
"""
import argparse
import ast
import hashlib
import json
import multiprocessing
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import time


class HTTPException(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        super().__init__(detail)


def helpers(path):
    source=path.read_bytes()
    tree=ast.parse(source.decode('utf-8-sig'))
    selected=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
              and node.name in {'_open_confined','_read_snapshot'}]
    if len(selected)!=2:raise RuntimeError('Expected exact upload helpers')
    namespace={'os':os,'Path':Path,'PurePosixPath':PurePosixPath,'stat':stat,'HTTPException':HTTPException,
               'MAX_SUBMISSION_PER_FILE_BYTES':4096}
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),namespace)
    return namespace['_read_snapshot'],hashlib.sha256(source).hexdigest()


def fifo_child(path,root,queue):
    read,_=helpers(path)
    try:read(root,PurePosixPath('fifo'),remaining=4096)
    except HTTPException as error:queue.put(error.status_code)
    else:queue.put('unexpected_read')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if os.name!='posix':raise SystemExit('This syscall probe requires Linux/POSIX')
    read,digest=helpers(args.source)
    checks={}
    with tempfile.TemporaryDirectory(prefix='lanshare-files-probe-') as directory:
        root=Path(directory)/'workspace'
        root.mkdir()
        (root/'normal.txt').write_bytes(b'synthetic artifact')
        checks['regular_file_snapshot']=read(root,PurePosixPath('normal.txt'),remaining=4096)==b'synthetic artifact'
        outside=Path(directory)/'outside.txt'
        outside.write_bytes(b'outside fixture')
        (root/'redirect.txt').symlink_to(outside)
        try:read(root,PurePosixPath('redirect.txt'),remaining=4096)
        except HTTPException as error:checks['symlink_denied']=error.status_code==403
        else:checks['symlink_denied']=False
        (root/'oversize').write_bytes(b'x'*4097)
        try:read(root,PurePosixPath('oversize'),remaining=4096)
        except HTTPException as error:checks['oversize_denied']=error.status_code==413
        else:checks['oversize_denied']=False
        os.mkfifo(root/'fifo',0o600)
        ctx=multiprocessing.get_context('fork')
        queue=ctx.Queue()
        process=ctx.Process(target=fifo_child,args=(args.source,root,queue))
        started=time.monotonic()
        process.start()
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join(2)
            checks['fifo_without_writer_denied_without_blocking']=False
        else:
            checks['fifo_without_writer_denied_without_blocking']=process.exitcode==0 and queue.get(timeout=1)==403
        elapsed=time.monotonic()-started
        queue.close()
    report={'kind':'exact_source_linux_filesystem_component_probe','source_sha256':digest,'checks':checks,
            'fifo_elapsed_seconds':round(elapsed,4),'paid_model_calls':0,'production_data_accessed':False,
            'full_authenticated_http_integration':False,'passed':all(checks.values())}
    encoded=json.dumps(report,indent=2)+'\n'
    if args.output:args.output.write_text(encoded,encoding='utf-8')
    print(encoded,end='')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
