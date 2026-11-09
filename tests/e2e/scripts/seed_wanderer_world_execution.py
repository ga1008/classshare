"""Seed real uncertain native execution plus a retryable event in a guarded UI fixture."""
import argparse,asyncio,json,sys,uuid
from pathlib import Path
repo=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-root',required=True,type=Path);runtime=p.parse_args().runtime_root.resolve()
assert runtime.is_relative_to(repo/'.codex-temp') and runtime!=repo/'.codex-temp'
f=json.loads((runtime/'fixture.json').read_text(encoding='utf8'));assert f['uiV3Synthetic'] and f['wandererWorld'] and Path(f['databasePath'])==runtime/'db/classroom.db'
if f.get('wandererWorkflow'):raise SystemExit('This owned fixture already has a workflow; create a fresh one to repeat acceptance.')
from tools.isolated_environment import isolate_sqlite_environment,guard_dotenv_loading,guard_postgres_connections
isolate_sqlite_environment(runtime);guard_dotenv_loading();guard_postgres_connections()
from classroom_app.database import get_db_connection
from classroom_app.services import wanderer_platform_service as platform
from classroom_app.services.wanderer_runtime_service import enqueue_wake_event,_claim
from classroom_app.services.agent_platform_route_capability import route_key
from classroom_app.time_utils import local_iso
from tests.test_wanderer_platform_execution import fixture_app
wid=f['wandererWorld']['id'];op=str(uuid.uuid4())
with get_db_connection() as c:
 c.execute('CREATE TABLE synthetic_owned_items(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,name TEXT,version INTEGER)')
 e=enqueue_wake_event(c,wid,'synthetic_ui',op,{'notice':'隔离界面验收；真实原生写入已发生，响应故意中断'})
 run=_claim(c,e['event_id']);c.commit()
command={'id':'uncertain-fixture','capability':route_key('POST','/api/owned-items-uncertain'),'args':{'body':{'name':op}}}
try:
 asyncio.run(platform.execute_platform_command(wanderer_id=wid,**{k:run[k] for k in ('turn_id','life_epoch','fencing_token','authority_version')},app=fixture_app(),conn_factory=get_db_connection,operation_id=op,command=command))
except RuntimeError:pass
else:raise AssertionError('Expected an uncertain native result')
with get_db_connection() as c:
 c.execute("INSERT INTO wanderer_commands(operation_id,turn_id,command_id,capability,args_hash,command_json,state,receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,'uncertain',?,?,?)",(op,run['turn_id'],'uncertain-fixture',command['capability'],'synthetic',json.dumps(command),json.dumps({'status':'uncertain','operation_id':op}),local_iso(),local_iso()))
 c.execute("UPDATE wanderer_turns SET state='completed',completed_at=? WHERE turn_id=?",(local_iso(),run['turn_id']))
 c.execute("UPDATE wanderer_wake_events SET state='failed' WHERE event_id=?",(e['event_id'],));c.commit()
f['wandererWorkflow']={'operationId':op,'eventId':e['event_id']};(runtime/'fixture.json').write_text(json.dumps(f,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'workflow_seeded':True}))
