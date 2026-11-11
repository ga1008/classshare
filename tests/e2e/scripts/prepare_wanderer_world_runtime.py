"""Create a disposable wanderer UI fixture with no real database or model."""
import sys,json,os,datetime,argparse,importlib.util
from pathlib import Path
repo=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime-root',type=Path,required=True)
runtime=parser.parse_args().runtime_root.resolve()
spec=importlib.util.spec_from_file_location('wanderer_ui_base',Path(__file__).with_name('prepare_ui_v3_runtime.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base);base.prepare(runtime)
from tools.isolated_environment import isolate_sqlite_environment,guard_dotenv_loading,guard_postgres_connections
isolate_sqlite_environment(runtime);guard_dotenv_loading();guard_postgres_connections()
f=json.loads((runtime/'fixture.json').read_text(encoding='utf8'));assert f['uiV3Synthetic'] and Path(f['databasePath'])==runtime/'db/classroom.db'
from classroom_app.database import get_db_connection
from classroom_app.services.wanderer_persona_seeds import seed_wanderers
from classroom_app.services.wanderer_identity_service import load_wanderer_by_slug,build_wanderer_user_dict
from classroom_app.services.wanderer_autobiography_service import write_memory
from classroom_app.services.wanderer_relationship_service import set_affinity
from classroom_app.services.wanderer_resource_service import consume
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
from classroom_app.services.social_common import dm_key
from classroom_app.services.social_group_service import create_group
from classroom_app.services.social_message_service import send_message
from classroom_app.services import social_message_service as sms
# Fixture seeding only: avoid sleeping for the ordinary per-minute send limit.
sms._enforce_rate_limit=lambda *args: None
from classroom_app.time_utils import local_iso
with get_db_connection() as c:
 seed_wanderers(c);wanderer_runtime_setting_set(c,'enabled','0')
 a=load_wanderer_by_slug(c,'linwanzhou');b=load_wanderer_by_slug(c,'atai')
 now=local_iso();wid=a['id'];bid=b['id'];ia=f"student:{a['student_id']}";ib=f"student:{b['student_id']}"
 for idx in range(37):
  write_memory(c,a,{'title':f'第{idx+1}次观察','content':f'我在公开广场看到了清晨的讨论，第{idx+1}次把它记了下来。\n'+('我想慢慢理解这个世界，也珍惜与朋友交流的机会。'*(8 if idx==36 else 1)),'location':'公开广场','occurred_at':(datetime.datetime.now()-datetime.timedelta(hours=38-idx)).isoformat(timespec='seconds'),'visibility':'public'},turn_id='synthetic-ui',operation_id=f'ui-memory-{idx}',scope={'kind':'public'},available_evidence=[])
 write_memory(c,a,{'content':'PRIVATE_MEMORY_SENTINEL_只在管理查看','location':'个人心绪','visibility':'private'},turn_id='synthetic-ui-private',operation_id='ui-private',scope={'kind':'private'},available_evidence=[])
 set_affinity(c,a,ib,100,expected_revision=0,source_turn_id='synthetic-ui',operation_id='ui-affinity-a',reason='欣赏他的坦率。')
 set_affinity(c,b,ia,100,expected_revision=0,source_turn_id='synthetic-ui',operation_id='ui-affinity-b',reason='愿意一起研读。')
 c.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",('ui-bond',f'{min(wid,bid)}:{max(wid,bid)}',min(wid,bid),max(wid,bid),'active','ui-request','ui-request',now,now))
 c.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) VALUES (?,?,?,?,?)",(*sorted([ia,ib]),'accepted',ia,now))
 g=create_group(c,build_wanderer_user_dict(a),'共读与长谈',[ib]);key=g['conversation_key']
 for idx in range(35):
  body = '同文不同ID的历史记录必须保留，不能在管理页藏起来。' if idx >= 33 else f'第{idx+1}条真实合成消息：今天读到了新的篇章。\n'+'完整原文，不截断。'*12
  send_message(c,build_wanderer_user_dict(a if idx%2==0 else b),key,{'body':body,'client_nonce':f'ui-message-{idx}'})
 private_key=dm_key(ia,ib)
 for idx in range(4):
  send_message(c,build_wanderer_user_dict(a if idx%2==0 else b),private_key,{'body':f'私聊合成往返{idx+1}：这是完整的好友原文。','client_nonce':f'ui-private-message-{idx}'})
 consume(c,wid,'ai_wake',7,'ui-wake');consume(c,wid,'web_search',1,'ui-search')
 c.commit();f['wandererWorld']={'id':wid,'studentId':a['student_id'],'slug':a['slug'],'groupKey':key,'dmKey':private_key,'privateSentinel':'PRIVATE_MEMORY_SENTINEL'}
(runtime/'fixture.json').write_text(json.dumps(f,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'seeded':True,'wanderer':wid,'memory':38,'messages':35}))
