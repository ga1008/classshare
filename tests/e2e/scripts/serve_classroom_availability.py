"""Real app/DB/routes with an in-process synthetic JWXT transport only."""
import argparse,asyncio,json,runpy,sqlite3,sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--runtime-root',required=True,type=Path)
args,_=parser.parse_known_args();runtime=args.runtime_root.resolve()
assert runtime.is_relative_to(ROOT/'.codex-temp') and runtime!=ROOT/'.codex-temp'
fixture=json.loads((runtime/'fixture.json').read_text('utf-8'))
assert fixture.get('classroomAvailability',{}).get('synthetic') and Path(fixture['databasePath']).resolve()==runtime/'db/classroom.db'
from tools.isolated_environment import isolate_sqlite_environment,guard_dotenv_loading,guard_postgres_connections
isolate_sqlite_environment(runtime);guard_dotenv_loading();guard_postgres_connections()
import httpx
from classroom_app.services import academic_classroom_sync_service as rooms
with sqlite3.connect(fixture['databasePath']) as fixture_db:
    fixture_school=fixture_db.execute('SELECT school_code FROM teachers WHERE id=?',(fixture['teacher']['id'],)).fetchone()[0]

def control():return json.loads((runtime/'availability-control.json').read_text('utf-8'))

async def respond(request):
    state=control();form={key:values[-1] for key,values in parse_qs(request.content.decode() if request.method=='POST' else request.url.query.decode(),keep_blank_values=True).items()}
    with (runtime/'academic-requests.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'method':request.method,'path':request.url.path,'form':form,'mode':state['mode']},ensure_ascii=False)+'\n')
    if request.url.path.endswith('cdjy_cxXqjc.html'):
        return httpx.Response(200,json={'lhList':[{'JXLDM':'136','JXLMC':'知新楼'}], 'jcList':[{'JCMC':str(n),'SJD':'合成时段'} for n in range(1,12)]})
    if request.method=='POST' and 'doType=query' in str(request.url):
        if state.get('delay_ms'):await asyncio.sleep(state['delay_ms']/1000)
        if state['mode']=='remote_error' or (state['mode']=='target_error' and form.get('cd_id')):return httpx.Response(503,text='Synthetic academic maintenance')
        if state['mode']=='malformed':return httpx.Response(200,text='<html>合成错误页面</html>',headers={'content-type':'text/html'})
        if state['mode']=='expired':return httpx.Response(302,headers={'location':'/xtgl/login_slogin.html'})
        room_rows=[('136B310','B310','知新楼B310'),('136B312','B312','知新楼B312')]
        if state['mode'] in {'pages','target_error'}:
            room_rows=[(f'QA-{n:03}',f'A{n:03}',f'知新楼A{n:03}') for n in range(1,124)]
            room_rows.insert(42,('136B312','B312','知新楼B312'));room_rows.append(('136B310','B310','知新楼B310'))
        items=[] if state['mode']=='empty' else [{'cd_id':rid,'cdbh':code,'cdmc':name,'xqh_id':'1','xqmc':'五合校区','lh':'136','jxlmc':'知新楼','cdlb_id':'03','cdlbmc':'实验室','zws':'62','sfkpk':'1'} for rid,code,name in room_rows]
        if form.get('cdmc'):items=[row for row in items if form['cdmc'] in row['cdmc']]
        if form.get('cd_id'):items=[row for row in items if form['cd_id']==row['cd_id']]
        total=len(items);size=int(form.get('queryModel.showCount') or 100);page=int(form.get('queryModel.currentPage') or 1)
        return httpx.Response(200,json={'items':items[(page-1)*size:page*size],'totalResult':total,'totalPage':(total+size-1)//size})
    return httpx.Response(200,text='<html>合成教务入口</html>',headers={'content-type':'text/html'})

@asynccontextmanager
async def authenticated(_credentials):
    if control()['mode']=='login_error':raise ValueError('合成教务登录已过期，请重新验证账号。')
    async with httpx.AsyncClient(base_url='https://academic.synthetic.invalid',transport=httpx.MockTransport(respond)) as client:
        yield client,SimpleNamespace(school_code='gxufl',base_url=client.base_url),{'status':'verified'}

rooms.open_authenticated_academic_client=authenticated
rooms.load_teacher_academic_access_method=lambda conn,teacher_id: {'synthetic':True,'school_code':fixture_school} if teacher_id==fixture['teacher']['id'] else None
runpy.run_path(str(ROOT/'tests/e2e/scripts/serve_ui_v3.py'),run_name='__main__')
