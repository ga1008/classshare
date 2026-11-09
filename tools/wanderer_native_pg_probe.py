"""Create, test and stop our own loopback-only synthetic PostgreSQL cluster."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from tools.isolated_environment import isolate_sqlite_environment,guard_dotenv_loading
    target=ROOT/".codex-temp"/("wanderer-native-pg-"+time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6])
    target.mkdir(parents=True,exist_ok=False)
    cluster=target/"cluster"
    runtime=target/"runtime"
    (runtime/"db").mkdir(parents=True)
    isolate_sqlite_environment(runtime)
    guard_dotenv_loading()
    binary=Path(shutil.which("pg_ctl") or r"C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe")
    initdb=binary.parent/"initdb.exe"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
    if port==5432: raise RuntimeError("Refuse production default port")
    flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
    def command(args):
        # A postmaster may inherit a Windows pipe handle from pg_ctl. File
        # handles avoid waiting for that background server to close stdout.
        with (target/"cluster-commands.log").open("ab") as out:
            completed=subprocess.run(args,stdout=out,stderr=out,creationflags=flags,timeout=60)
        completed.check_returncode()
    started=False
    report={"kind":"synthetic_native_postgres","cluster":str(cluster),"loopback_only":True,"port":port,"business_data_used":False}
    try:
        command([str(initdb),"-D",str(cluster),"-U","rehearsal_admin","--auth=trust","--encoding=UTF8","--no-locale"])
        command([str(binary),"-D",str(cluster),"-l",str(target/"postgres.log"),"-o",f"-h 127.0.0.1 -p {port}","-w","start"])
        started=True
        import psycopg
        with psycopg.connect(host="127.0.0.1",port=port,user="rehearsal_admin",dbname="postgres",autocommit=True) as conn:
            identity=conn.execute("SELECT current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port')").fetchone()
            if Path(identity[0]).resolve()!=cluster.resolve() or identity[1]!="127.0.0.1" or int(identity[2])!=port:
                raise RuntimeError("Synthetic cluster identity mismatch")
            conn.execute("CREATE DATABASE lanshare_assessment_rehearsal TEMPLATE template0")
        os.environ.update(ASSESSMENT_REHEARSAL_TEST_CLUSTER=str(cluster),ASSESSMENT_REHEARSAL_TEST_PORT=str(port))
        suite=unittest.defaultTestLoader.loadTestsFromName("tests.test_wanderer_world_postgres")
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName("tests.test_wanderer_economy_native_postgres"))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName("tests.test_agent_authority_postgres"))
        with (target/"tests.log").open("w",encoding="utf-8") as stream:
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        report.update(status="passed" if result.wasSuccessful() else "failed",tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped))
        return 0 if result.wasSuccessful() else 1
    except Exception as exc:
        report.update(status="error",error=type(exc).__name__+": "+str(exc))
        raise
    finally:
        if started or (cluster/"postmaster.pid").exists():
            command([str(binary),"-D",str(cluster),"-m","fast","-w","stop"])
        report["cluster_stopped"]=not (cluster/"postmaster.pid").exists()
        (target/"result.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report))


if __name__=="__main__":
    raise SystemExit(main())
