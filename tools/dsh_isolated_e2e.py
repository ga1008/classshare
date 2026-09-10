#!/usr/bin/env python3
"""Explicitly isolated Linux DSH acceptance harness; never a deployment tool.

Production access is limited to immutable image layers, schema-only pg_dump,
and a read-only active-key query whose result is reencrypted before transfer.
All mutable resources are fixed to the dated, unprivileged fixture namespace.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.dsh_e2e_cohort import Cohort, LAB as LAB_PATH, names, verify_inputs, sha256

LAB = Path(str(LAB_PATH))
COHORT = None
ROOT = INPUTS = APP_IMAGE = DSH_IMAGE = ARCHIVE_SHA = PG = APP = NET = DATABASE = COPY = PORT = None


def configure(cohort):
    global COHORT, ROOT, INPUTS, APP_IMAGE, DSH_IMAGE, ARCHIVE_SHA, PG, APP, NET, DATABASE, COPY, PORT
    COHORT = cohort
    derived = names(cohort.name)
    ROOT, INPUTS = Path(derived['root']), Path(derived['inputs'])
    APP_IMAGE, DSH_IMAGE, ARCHIVE_SHA, PORT = cohort.app_image, cohort.dsh_image, cohort.archive_sha256, cohort.port
    PG, APP, NET, DATABASE, COPY = (derived[key] for key in ('pg', 'app', 'network', 'database', 'copy'))


def verify_frozen_source():
    manifest = verify_inputs(COHORT)
    for name, digest in manifest['files'].items():
        target = ROOT / 'source' / name
        if not target.is_file() or target.is_symlink() or sha256(target) != digest:
            raise RuntimeError('Prepared source has changed since the frozen cohort')
    if any((ROOT / 'source' / name).exists() for name in manifest.get('removed', [])):
        raise RuntimeError('Retired source remains in the prepared cohort')
    return manifest


def run(*args, data=None, timeout=120):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        # Commands may carry credentials. Never echo argv or captured stdout.
        (ROOT / 'last-error.txt').write_bytes(result.stderr[-8000:])
        os.chmod(ROOT / 'last-error.txt', 0o600)
        raise RuntimeError('Isolated command failed; inspect private last-error.txt')
    return result.stdout


def write_private(path, data):
    with open(path, 'w', encoding='utf-8') as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(data)


def docker_base():
    return ['docker', 'run', '--rm', '--network', NET, '--env-file', str(ROOT/'app.env'),
            '--memory', '640m', '--cpus', '1', '--pids-limit', '256',
            '--mount', f'type=bind,src={ROOT}/source,dst=/app,readonly',
            '--mount', f'type=bind,src={ROOT}/data,dst=/e2e-data',
            '--mount', f'type=bind,src={ROOT}/private,dst=/e2e-private,readonly',
            '--mount', f'type=bind,src={ROOT}/ipc/control,dst=/e2e-control',
            '--workdir', '/app', '--entrypoint', 'python']


def prepare():
    manifest = verify_inputs(COHORT)
    for name in ('dsh_isolated_e2e.py','dsh_isolated_e2e_app.py','dsh_e2e_cohort.py'):
        if sha256(Path(__file__).resolve().parent/name) != manifest['files']['tools/'+name]:
            raise RuntimeError('Uploaded harness does not match the frozen source archive')
    if ROOT.exists():
        raise RuntimeError('Refusing to replace an existing E2E workspace')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORT))
    for name in (PG, APP, COPY, COPY+'-evidence'):
        if subprocess.run(['docker', 'inspect', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            raise RuntimeError('Fixture container already exists')
    if subprocess.run(['docker', 'network', 'inspect', NET], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('Fixture network already exists')
    ROOT.mkdir(mode=0o700)
    write_private(ROOT/'cohort-config.json', json.dumps(COHORT.plan(), sort_keys=True))
    for name in ('source', 'private', 'data', 'ipc', 'ipc/control', 'state', 'pgdata'):
        (ROOT/name).mkdir(mode=0o700, parents=True, exist_ok=True)
    evidence=json.loads(run('docker','run','--rm','--name',COPY+'-evidence','--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','128m','--cpus','0.25','--pids-limit','32',DSH_IMAGE,'--evidence'))
    if evidence!={'dsh_package_version':'0.1.5-rc.1','profile_sha256':COHORT.profile_sha256}:
        raise RuntimeError('Pinned DSH image/profile evidence mismatch; no database/key transfer attempted')
    archive = INPUTS/'source.tar.gz'
    run('docker', 'create', '--name', COPY, APP_IMAGE)
    run('docker', 'cp', COPY+':/app/.', str(ROOT/'source'), timeout=180)
    run('docker', 'rm', COPY)
    seen = set()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.name not in manifest['files'] or member.name in seen or (ROOT/'source') not in (ROOT/'source'/member.name).resolve().parents:
                raise RuntimeError('Unexpected archive entry')
            seen.add(member.name)
            target = ROOT/'source'/member.name
            if target.is_symlink() or any(path.is_symlink() for path in target.parents if path != ROOT.parent):
                raise RuntimeError('Image source symlink cannot receive overlay')
            target.parent.mkdir(parents=True, exist_ok=True)
            content = tar.extractfile(member).read()
            assert hashlib.sha256(content).hexdigest() == manifest['files'][member.name]
            target.write_bytes(content)
    if seen != set(manifest['files']):
        raise RuntimeError('Archive omitted frozen source files')
    for name in manifest.get('removed', []):
        target = ROOT/'source'/name
        if target.is_symlink() or (ROOT/'source') not in target.resolve().parents:
            raise RuntimeError('Retired source path is redirected')
        if target.exists():
            if not target.is_file(): raise RuntimeError('Retired source is not a regular file')
            target.unlink()
    # The tested profile contains no credentials and is immutable to runners.
    shutil.copytree(INPUTS/'profile', ROOT/'profile')
    for path in [ROOT/'profile', *(ROOT/'profile').rglob('*')]:
        if path.is_symlink(): raise RuntimeError('Unexpected profile symlink')
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    pg_password, app_password, secret_key = (secrets.token_hex(32) for _ in range(3))
    write_private(ROOT/'pg.env', f'POSTGRES_USER=e2e_bootstrap\nPOSTGRES_PASSWORD={pg_password}\nPOSTGRES_DB=postgres\n')
    env = {'PYTHON_DOTENV_DISABLED':'1', 'PYTHONDONTWRITEBYTECODE':'1', 'DB_ENGINE':'postgres',
           'POSTGRES_BACKEND_READY':'true', 'DATABASE_URL':f'postgresql://e2e_app:{app_password}@{PG}:5432/{DATABASE}',
           'DSH_E2E_COHORT':COHORT.name,
           'SECRET_KEY':secret_key, 'LANSHARE_DATA_ROOT':'/e2e-data', 'MAIN_DATA_DIR':'/e2e-data',
           'AGENT_TASKS_ENABLED':'true', 'AGENT_DSH_ENABLED':'true', 'AGENT_DSH_LAUNCHER_SOCKET':'/e2e-control/launcher.sock',
           'AGENT_TASK_MAX_RUNTIME_SECONDS':'540', 'AGENT_TASK_GLOBAL_CONCURRENCY':'1',
           'AGENT_MODEL_DEFAULT':'deepseek-v4-pro', 'AGENT_MODEL_SEARCH_MODEL':'deepseek-v4-flash',
           'AI_ASSISTANT_URL':'http://127.0.0.1:9', 'AI_DURABLE_JOBS_ENABLED':'false', 'CAREER_JOBS_ENABLED':'false',
           'POSTGRES_POOL_MIN':'1', 'POSTGRES_POOL_MAX':'4', 'POSTGRES_POOL_CHECKOUT_TIMEOUT':'3', 'MAIN_WORKERS':'1'}
    write_private(ROOT/'app.env', ''.join(f'{key}={value}\n' for key,value in env.items()))
    fixture = {'teacher':{'id':900001,'email':'teacher@dsh-e2e.example.invalid','password':secrets.token_urlsafe(24)},
               'admin':{'id':900002,'email':'admin@dsh-e2e.example.invalid','password':secrets.token_urlsafe(24)},
               'student':{'id':900001,'identifier':'DSH-E2E-900001','password':secrets.token_urlsafe(24)}}
    write_private(ROOT/'private/fixture.json', json.dumps(fixture))
    pg_image = run('docker','inspect','--format','{{.Image}}','lanshare-postgres-1').decode().strip()
    run('docker','network','create',NET)
    run('docker','run','-d','--name',PG,'--network',NET,'--env-file',str(ROOT/'pg.env'),
        '--memory','256m','--cpus','0.5','--pids-limit','100','--mount',f'type=bind,src={ROOT}/pgdata,dst=/var/lib/postgresql/data',
        pg_image,'postgres','-c','shared_buffers=32MB','-c','max_connections=20')
    for _ in range(60):
        if subprocess.run(['docker','exec',PG,'pg_isready','-h','127.0.0.1','-U','e2e_bootstrap'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0: break
        time.sleep(.5)
    complete_prepare()


def complete_prepare():
    from urllib.parse import urlsplit
    verify_frozen_source()
    env = dict(line.split('=',1) for line in (ROOT/'app.env').read_text().splitlines())
    app_password = urlsplit(env['DATABASE_URL']).password
    secret_key = env['SECRET_KEY']
    info=json.loads(run('docker','inspect',PG))[0]
    assert any(m['Source']==str(ROOT/'pgdata') and m['Destination']=='/var/lib/postgresql/data' for m in info['Mounts'])
    assert set(info['NetworkSettings']['Networks'])=={NET}
    # CREATE statements intentionally fail if a partial prior seed created a DB.
    sql = f"CREATE ROLE e2e_app LOGIN PASSWORD '{app_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;\nCREATE DATABASE {DATABASE} OWNER e2e_app;\nREVOKE CONNECT ON DATABASE postgres FROM PUBLIC;\nREVOKE CONNECT ON DATABASE template1 FROM PUBLIC;\n"
    run('docker','exec','-i',PG,'psql','-X','-v','ON_ERROR_STOP=1','-U','e2e_bootstrap','-d','postgres',data=sql.encode())
    schema = run('docker','exec','lanshare-postgres-1','sh','-c',
                 'exec pg_dump --schema-only --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"',timeout=120)
    (ROOT/'schema-only.sql').write_bytes(schema)
    os.chmod(ROOT/'schema-only.sql',0o600)
    run('docker','exec','-i',PG,'psql','-X','-v','ON_ERROR_STOP=1','-U','e2e_bootstrap','-d',DATABASE,
        data=b'SET ROLE e2e_app;\n'+schema,timeout=120)
    # Query and decryption occur in the trusted existing app. No source key or
    # infrastructure credential leaves it; only target-encrypted key is returned.
    transfer = '''import json,sys,os,hashlib
import psycopg
from psycopg.rows import dict_row
from classroom_app.services import email_notification_service as crypto
target=json.load(sys.stdin)
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,options='-c default_transaction_read_only=on') as conn:
 row=conn.execute("SELECT id,key_encrypted,base_url,model FROM agent_runtime_api_keys WHERE provider='deepseek' AND enabled=1 AND is_active=1 ORDER BY updated_at DESC,id DESC LIMIT 1").fetchone()
 assert row and row['id']==target['expected_key_id'], 'Active service key identity changed'
 secret=crypto.decrypt_secret(row['key_encrypted']); assert secret
 crypto.SECRET_KEY=target['secret']
 print(json.dumps({'key_encrypted':crypto.encrypt_secret(secret),'key_fingerprint':hashlib.sha256(secret.encode()).hexdigest(),'base_url':row['base_url'],'model':row['model']}))
'''
    encrypted = run('docker','exec','-i','lanshare-app-1','python','-c',transfer,data=json.dumps({'secret':secret_key,'expected_key_id':COHORT.expected_key_id}).encode())
    imported=json.loads(encrypted)
    assert imported['key_encrypted'].startswith('v1:')
    write_private(ROOT/'private/model-import.json',json.dumps(imported))
    finish_seed()


def finish_seed():
    manifest=verify_frozen_source()
    schema=(ROOT/'schema-only.sql').read_bytes()
    run(*docker_base(),APP_IMAGE,'tools/dsh_isolated_e2e_app.py','seed',timeout=120)
    report={'scope':'isolated synthetic E2E, not production deployment','source_archive_sha256':ARCHIVE_SHA,
            'cohort':COHORT.name,'source_commit':COHORT.source_commit,'manifest_sha256':COHORT.manifest_sha256,
            'profile_sha256':COHORT.profile_sha256,
            'source_file_count':len(manifest['files']),'app_image':APP_IMAGE,'dsh_image':DSH_IMAGE,
            'source_overrides':{name:hashlib.sha256((ROOT/'source'/name).read_bytes()).hexdigest()
                for name,digest in manifest['files'].items() if (ROOT/'source'/name).is_file()
                and hashlib.sha256((ROOT/'source'/name).read_bytes()).hexdigest()!=digest},
            'schema_only_sha256':hashlib.sha256(schema).hexdigest(), 'production_business_rows_copied':0,
            'production_user_sessions_used':0,'production_database_writes':0,
            'separate_pg_container':PG,'dedicated_database':DATABASE,'dedicated_network':NET,'app_bind':f'127.0.0.1:{PORT}',
            'app_role':'e2e_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
            'lifespan':'off; explicit init_database; no automatic workers',
            'production_key_transfer':'trusted read-only query; reencrypted with independent secret; never runner-mounted',
            'state':'prepared'}
    (ROOT/'isolation-evidence.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


def start():
    verify_frozen_source()
    assert (ROOT/'isolation-evidence.json').exists()
    output=open(ROOT/'launcher.log','ab',buffering=0)
    command=['python3',str(ROOT/'source/tools/agent_dsh_launcher.py'),'--image',DSH_IMAGE,
             '--profile',str(ROOT/'profile'),'--task-root',str(ROOT/'data/agent_tasks'),
             '--state-root',str(ROOT/'state'),'--socket-root',str(ROOT/'ipc'),
             '--max-concurrency','1','--upstream-port',str(PORT)]
    proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
    (ROOT/'launcher.pid').write_text(str(proc.pid))
    for _ in range(60):
        if (ROOT/'ipc/control/launcher.sock').exists(): break
        if proc.poll() is not None: raise RuntimeError('Isolated launcher startup failed')
        time.sleep(.2)
    command=docker_base()
    command.remove('--rm')
    command[2:2]=['-d','--name',APP,'-p',f'127.0.0.1:{PORT}:8000']
    run(*command,APP_IMAGE,'-m','uvicorn','classroom_app.app:app','--host','0.0.0.0','--port','8000','--workers','1','--lifespan','off')
    print(json.dumps({'isolated_app_started':APP,'launcher_pid':proc.pid,'port':PORT}))


def attest():
    names=[APP,PG]
    names+=run('docker','ps','--filter','label=lanshare.agent.runtime=deepseek-dsh','--format','{{.Names}}').decode().splitlines()
    evidence=[]
    for name in names:
        item=json.loads(run('docker','inspect',name))[0]
        mounts=[{'source':m['Source'],'destination':m['Destination'],'read_only':not m['RW']} for m in item['Mounts']]
        if name not in (APP,PG) and not any(m['source'].startswith(str(ROOT)+'/data/agent_tasks/tasks/') for m in mounts): continue
        config=item['HostConfig']
        record={'name':name,'image':item['Image'],'networks':list(item['NetworkSettings']['Networks']),
                'network_mode':config['NetworkMode'],'read_only_root':config['ReadonlyRootfs'],
                'memory':config['Memory'],'pids_limit':config['PidsLimit'],'cap_drop':config['CapDrop'],
                'security_options':config['SecurityOpt'],'mounts':mounts,
                'environment_names':sorted(e.split('=',1)[0] for e in item['Config'].get('Env',[])),
                'ports':item['NetworkSettings']['Ports']}
        if name not in (APP,PG):
            record['checks']={'network_none':record['network_mode']=='none','immutable_image':record['image']==DSH_IMAGE,
                              'read_only_root':record['read_only_root'],
                              'no_database_credentials':not set(record['environment_names']) & {'DATABASE_URL','SECRET_KEY','POSTGRES_PASSWORD','DEEPSEEK_API_KEY'},
                              'no_production_mounts':all(m['source'].startswith(str(ROOT)+'/') for m in mounts),
                              'no_docker_socket':all(m['destination']!='/var/run/docker.sock' for m in mounts)}
        evidence.append(record)
    path=ROOT/('container-boundaries-'+str(int(time.time()))+'.json')
    path.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'evidence_file':str(path),'containers':evidence}))


def quiesce():
    import signal
    checks=json.loads((ROOT/'data/e2e-lifecycle-boundaries.json').read_text())['checks']
    assert all(checks.values())
    for name in run('docker','ps','--filter','label=lanshare.agent.runtime=deepseek-dsh','--format','{{.Names}}').decode().splitlines():
        item=json.loads(run('docker','inspect',name))[0]
        if any(m['Source'].startswith(str(ROOT)+'/') for m in item['Mounts']):
            raise RuntimeError('An isolated runner is still active')
    for name in (APP,PG):
        item=json.loads(run('docker','inspect',name))[0]
        assert set(item['NetworkSettings']['Networks'])=={NET}
        assert all(m['Source'].startswith(str(ROOT)+'/') for m in item['Mounts'])
        run('docker','stop','--time','15',name,timeout=25)
    pid=int((ROOT/'launcher.pid').read_text())
    process=Path('/proc')/str(pid)
    if process.exists():
        command=(process/'cmdline').read_bytes().split(b'\0')
        assert str(ROOT/'source/tools/agent_dsh_launcher.py').encode() in command
        assert str(ROOT/'state').encode() in command
        os.kill(pid,signal.SIGTERM)
        for _ in range(50):
            if not process.exists() or (process/'stat').read_text().split()[2]=='Z': break
            time.sleep(.1)
        else: raise RuntimeError('Launcher has not stopped; retained isolated resources')
    record={'containers_stopped':[APP,PG],'launcher_stopped':True,'active_isolated_runners':0,
            'data_preserved':str(ROOT),'production_services_changed':False}
    (ROOT/'shutdown-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','resume-prepare','finish-seed','start','attest','quiesce','teacher','student','admin','report','boundaries'])
    parser.add_argument('--cohort',required=True)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--app-image',required=True)
    parser.add_argument('--dsh-image',required=True)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--archive-sha256',required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--profile-sha256',required=True)
    parser.add_argument('--expected-key-id',type=int,required=True)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--check-inputs',type=Path,help='Local read-only input checks; only with --dry-run')
    parser.add_argument('--allow-paid',action='store_true',help='Explicitly allow the chosen synthetic actor model run')
    args=parser.parse_args()
    cohort=Cohort(args.cohort,args.port,args.app_image,args.dsh_image,args.source_commit,args.archive_sha256,args.manifest_sha256,args.profile_sha256,args.expected_key_id)
    configure(cohort)
    if args.dry_run:
        plan=cohort.plan()
        plan.update({'phase':args.phase,'dry_run':True,'docker_commands_run':0,'production_reads':0,'model_requests':0})
        if args.check_inputs:
            plan['verified_input_files']=len(verify_inputs(cohort,args.check_inputs)['files'])
        print(json.dumps(plan,indent=2))
        return
    if args.check_inputs:
        parser.error('--check-inputs is only available with --dry-run')
    if args.phase in {'teacher','student','admin'} and not args.allow_paid:
        parser.error('A real model run requires explicit --allow-paid; use --dry-run to inspect the cohort first')
    if sys.platform!='linux' or os.getuid()!=0 or LAB.resolve(strict=True)!=LAB or LAB.is_symlink():
        raise RuntimeError('Requires exact root-owned isolated Linux lab')
    if ROOT.parent != LAB or ROOT == LAB/'e2e' or ROOT.is_symlink() or ROOT.resolve()!=ROOT:
        raise RuntimeError('Cohort directory is redirected or overlaps old E2E')
    if args.phase!='prepare':
        if json.loads((ROOT/'cohort-config.json').read_text())!=cohort.plan():
            raise RuntimeError('Cohort immutable configuration differs from prepare')
    if args.phase=='prepare': prepare()
    elif args.phase=='resume-prepare': complete_prepare()
    elif args.phase=='finish-seed': finish_seed()
    elif args.phase=='start': start()
    elif args.phase=='attest': attest()
    elif args.phase=='quiesce': quiesce()
    else:
        verify_frozen_source()
        value=run('docker','exec',APP,'python','tools/dsh_isolated_e2e_app.py',args.phase,timeout=650)
        print(value.decode())


if __name__=='__main__': main()
