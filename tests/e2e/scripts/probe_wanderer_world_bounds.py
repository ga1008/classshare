"""Bounded SQLite workload probe, synthetic data only; no model calls or HTTP."""
import argparse,asyncio,json,sys,time
from pathlib import Path
from unittest.mock import patch
repo=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-root',required=True,type=Path);runtime=p.parse_args().runtime_root.resolve()
assert runtime.is_relative_to(repo/'.codex-temp') and runtime!=repo/'.codex-temp' and not runtime.exists()
from tools.isolated_environment import isolate_sqlite_environment,guard_dotenv_loading,guard_postgres_connections
isolate_sqlite_environment(runtime);guard_dotenv_loading();guard_postgres_connections()
from classroom_app.database import init_database,get_db_connection
from classroom_app.config import ensure_runtime_directories
ensure_runtime_directories();init_database()
from classroom_app.services.wanderer_persona_seeds import seed_wanderers
from classroom_app.services.wanderer_identity_service import load_wanderer
from classroom_app.services.wanderer_autobiography_service import timeline
from classroom_app.services.wanderer_workspace_service import workspace_page
from classroom_app.services.wanderer_admin_service import list_wanderer_cards
from classroom_app.services import wanderer_runtime_service as runtime_service
from classroom_app.time_utils import local_iso
stamp=local_iso();result={'synthetic':True,'database':str(runtime/'db/classroom.db'),'backend':'isolated SQLite','model_calls':0}
with get_db_connection() as c:
 c.execute("INSERT INTO teachers(name,email,hashed_password) VALUES('合成教师','perf@example.invalid','synthetic')")
 seed_wanderers(c)
 seed=dict(c.execute('SELECT * FROM wanderer_agents ORDER BY id LIMIT 1').fetchone());sid=int(seed['student_id'])
 cid=c.execute('SELECT class_id FROM students WHERE id=?',(sid,)).fetchone()[0]
 for n in range(100-int(c.execute('SELECT COUNT(*) FROM wanderer_agents').fetchone()[0])):
  student=c.execute("INSERT INTO students(student_id_number,name,class_id,enrollment_status) VALUES(?,?,?,'wanderer') RETURNING id",(f'PERF-W-{n}',f'合成散修{n}',cid)).fetchone()[0]
  row={k:v for k,v in seed.items() if k!='id'};row.update(student_id=student,slug=f'perf_w_{n}',display_name=f'合成散修{n}')
  c.execute(f"INSERT INTO wanderer_agents({','.join(row)}) VALUES({','.join('?' for _ in row)})",tuple(row.values()))
 wid=int(seed['id']);base=int(c.execute('SELECT COALESCE(MAX(id),0) FROM wanderer_episodic_memories').fetchone()[0])
 c.executemany('INSERT INTO wanderer_episodic_memories(id,wanderer_id,occurred_at,kind,location,title,content,source_kind,source_ref,episode_hash,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[(base+n,wid,stamp,'action','公开广场',f'合成经历{n}','这是一条隔离负载记录。'*15,'self',f'perf:{n}',f'perf:{n}','archived',stamp) for n in range(1,20001)])
 c.executemany('INSERT INTO wanderer_memory_sources(memory_id,wanderer_id,command_key,origin_author,turn_id,visibility,scope_json,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',[(base+n,wid,f'perf:{n}',wid,'synthetic','public' if n%2 else 'private','{"kind":"public"}' if n%2 else '{"kind":"private"}','[]',stamp,stamp) for n in range(1,20001)])
 agents=c.execute('SELECT id FROM wanderer_agents ORDER BY id').fetchall()
 for row in agents:
  for index in range(2):runtime_service.enqueue_wake_event(c,row['id'],'synthetic',f'perf:{row["id"]}:{index}')
 c.commit()
 w=load_wanderer(c,wid);metrics={}
 def measure(name,fn):
  queries=[];c.set_trace_callback(queries.append);start=time.perf_counter();value=fn();elapsed=(time.perf_counter()-start)*1000;c.set_trace_callback(None)
  metrics[name]={'elapsed_ms':round(elapsed,2),'sql_statements':len(queries),'returned':len(value.get('items',[])) if isinstance(value,dict) else len(value),'json_bytes':len(json.dumps(value,ensure_ascii=False).encode())}
  return value
 first=measure('admin_memory_page',lambda:workspace_page(c,wid,section='memory',limit=30))
 second=measure('admin_memory_next_page',lambda:workspace_page(c,wid,section='memory',limit=30,cursor=first['next_cursor']))
 assert len(first['items'])==len(second['items'])==30 and first['total']==20000
 assert not {x['id'] for x in first['items']} & {x['id'] for x in second['items']}
 public=measure('public_memory_page',lambda:timeline(c,w,viewer='public',limit=12));assert len(public['items'])==12 and all(x['visibility']=='public' for x in public['items'])
 cards=measure('admin_100_cards',lambda:list_wanderer_cards(c));assert len(cards)==100
 result.update(memory_records=20000,wanderers=100,pending_events=200,reads=metrics)
called=[]
async def no_model(factory,wanderer_id,**kwargs):called.append(wanderer_id);return {'status':'synthetic_no_model','ai_calls':0}
with patch.object(runtime_service,'wake_wanderer',side_effect=no_model):
 start=time.perf_counter();batch=asyncio.run(runtime_service.drain_wake_events(get_db_connection,limit=20));elapsed=(time.perf_counter()-start)*1000
 assert batch['processed']==len(called)==20
 called.clear();hard=asyncio.run(runtime_service.drain_wake_events(get_db_connection,limit=1000));assert hard['processed']==len(called)==100
result['queue']={'requested':20,'processed':batch['processed'],'elapsed_ms':round(elapsed,2),'max_requested':1000,'hard_cap_processed':hard['processed'],'worker':'stubbed before any model call'}
(runtime/'bounds-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(result,ensure_ascii=True))
