"""Fresh synthetic VMware lab HTTP recorder probe; no production endpoints."""
import argparse, asyncio, base64, collections, json, math, ssl, time, uuid,os,copy
from pathlib import Path
import sys
from urllib.parse import urlsplit

# Keep imports side effect free: isolation validation precedes network clients.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.platform_capacity_lab_manifest import _json, source_hashes
import httpx, websockets

def dist(values):
    v=sorted(values)
    return {"count":len(v),**({f"p{p}_ms":round(v[min(len(v)-1,math.ceil(len(v)*p/100)-1)],2) for p in (50,95,99)} if v else {}),"max_ms":round(max(v),2) if v else None}


def load_owned_handoff(work, source_root, base, nonce, release):
    """Refuse every target except an explicit pinned, source-matched lab.

    The client never receives a DB DSN. Physical PG/image ownership remains
    independently verified by the Linux controller and facts command.
    """
    import hashlib
    import re
    from tools.platform_capacity_lab_manifest import LabManifestError

    def require(ok, message):
        if not ok:
            raise LabManifestError(message)
    root = Path(source_root).resolve(strict=True)
    work = Path(work).resolve(strict=True)
    require(work.is_relative_to(root / ".codex-temp"), "Client work must stay under source-root/.codex-temp")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce or "") is not None, "Explicit owned nonce required")
    require(release == "capacity-linux-" + nonce, "Release must identify the owned nonce")
    try:
        origin = urlsplit(base)
        port = origin.port
    except (TypeError, ValueError):
        raise LabManifestError("Invalid lab origin") from None
    require(origin.scheme == "https" and origin.hostname in {"127.0.0.1", "::1"}
            and port is not None and 1024 <= port <= 65535
            and not origin.username and not origin.password and origin.path in {"", "/"}
            and not origin.query and not origin.fragment, "Only explicit loopback high-port HTTPS is allowed")
    handoff_root = (work / "client-handoff").resolve(strict=True)
    require(handoff_root.is_relative_to(work), "Handoff cannot escape owned work")
    handoff = _json(handoff_root / "handoff.json")
    require(handoff.get("kind") == "capacity-linux-controller-handoff"
            and handoff.get("schema_version") == 1 and handoff.get("synthetic") is True
            and handoff.get("lab_nonce") == nonce and handoff.get("release_id") == release,
            "Controller handoff nonce/release/synthetic identity mismatch")
    require(not any(key in handoff for key in ("database", "databasePath", "dsn", "database_url")),
            "Client cannot accept database coordinates")
    ca = (handoff_root / "capacity-lab.crt").resolve(strict=True)
    require(ca.is_relative_to(handoff_root)
            and hashlib.sha256(ca.read_bytes()).hexdigest() == handoff.get("ca_sha256"), "Pinned lab CA mismatch")
    hashes = source_hashes(root)
    require(bool(hashes) and hashes == handoff.get("client_source_sha256")
            and hashes.keys() == handoff.get("server_source_sha256", {}).keys(),
            "Complete backend source snapshot differs from the controller handoff")
    users = handoff.get("users")
    require(isinstance(users, list) and len(users) == 200 and all(
        isinstance(row, dict) and set(row) == {"identifier", "student_id"}
        and row["identifier"] == f"CAPACITY-SYNTHETIC-{index:04d}"
        and type(row["student_id"]) is int and row["student_id"] > 0
        for index, row in enumerate(users))
        and len({row["student_id"] for row in users}) == 200,
        "Exactly 200 distinct synthetic fixture identities required")
    require(type(handoff.get("offering_id")) is int and handoff["offering_id"] > 0,
            "Synthetic classroom identity required")
    reading = _json(handoff_root / "capacity-reading.json")
    social = _json(handoff_root / "capacity-social.json")
    require(reading.get("synthetic") is True and reading.get("class_offering_id") == handoff["offering_id"]
            and type(reading.get("material_id")) is int and reading["material_id"] > 0,
            "Synthetic reading fixture differs from the classroom")
    require(social.get("synthetic") is True and social.get("student_ids") == [row["student_id"] for row in users]
            and social.get("identifiers") == [row["identifier"] for row in users],
            "Social fixture differs from the synthetic cohort")
    from tools.platform_capacity_lab_manifest import fingerprint
    expected = handoff.get("expected_identity", {})
    require(expected.get("lab_nonce") == nonce and expected.get("release_id") == release
            and expected.get("users_sha256") == fingerprint(users)
            and expected.get("offering_id") == handoff["offering_id"]
            and expected.get("source_fingerprint") == fingerprint(handoff["server_source_sha256"]),
            "Controller published identity fingerprints differ")
    return handoff

async def main(args):
    work=Path(args.work).resolve(); h=load_owned_handoff(work, args.source_root, args.base, args.lab_nonce, args.release_id)
    if args.steady100 < 60 or args.steady200 < 600:
        raise ValueError('Capacity evidence requires at least 60s/600s steady phases')
    companion=_json(work/'client-handoff/recorder-second-offering.json')
    if companion.get('synthetic') is not True or companion.get('lab_nonce') != h['lab_nonce'] or companion.get('primary_offering_id') != h['offering_id'] or type(companion.get('second_offering_id')) is not int or companion['second_offering_id'] == h['offering_id']:
        raise ValueError('Explicit controller-owned companion classroom required')
    if args.ws_connections not in (400,800):
        raise ValueError('Use the declared 400/800 resident connection profiles')
    run=uuid.uuid4().hex; out=work/run;out.mkdir()
    process_begin=os.times();wall_begin=time.perf_counter();loop_stop=False;loop_lag=[];ws_open_samples=[];ws_expected_open=0
    def open_ws_count():return sum(getattr(getattr(conn,"state",None),"name",None)=="OPEN" for conn in ws_connections)
    async def sample_loop():
        while not loop_stop:
            target=time.perf_counter()+.5;await asyncio.sleep(.5);loop_lag.append(max(0,(time.perf_counter()-target)*1000))
            if ws_expected_open and not ws_planned and not ws_stopping:
                ws_open_samples.append({"epoch":time.time(),"expected":ws_expected_open,"actual_open":open_ws_count()})
    loop_sampler=asyncio.create_task(sample_loop())
    stats=collections.defaultdict(list);statuses=collections.Counter(); ledger=[];phases=[];faults=[];materials=[]
    # Preserve every planned/accepted item even if a later assertion aborts the
    # run. An incomplete ledger is evidence of failure, never a passing result.
    import atexit
    def save_partial_ledger():
        (out/'partial-request-ledger.json').write_text(json.dumps({'run_id':run,'offering_id':h['offering_id'],'users':h['users'],'batches':ledger,'materials':materials},ensure_ascii=False))
    atexit.register(save_partial_ledger)
    tokens={};logins={};active=0;peak=0; scheduled=0;sent=0
    completed_seen=set();material_contracts=[]
    ws_connections=[];ws_tasks=[];ws_counts=collections.Counter();ws_planned=False;ws_stopping=False;ws_slots=asyncio.Semaphore(16)
    ctx=ssl.create_default_context(cafile=work/'client-handoff/capacity-lab.crt')
    headers={'User-Agent':'LanShare synthetic recorder capacity probe'}
    async with httpx.AsyncClient(base_url=args.base,verify=ctx,trust_env=False,headers=headers,
            timeout=httpx.Timeout(60,pool=30),limits=httpx.Limits(max_connections=350,max_keepalive_connections=250)) as c:
        first=await c.get('/api/internal/live');assert first.status_code==200 and first.headers.get('x-lanshare-release')==h['release_id']
        async def req(label,method,path,token=None,**kwargs):
            nonlocal active,peak,sent
            active+=1;peak=max(peak,active);sent+=1;beg=time.perf_counter()
            try:
                request_headers={'Cookie':f'access_token={token}'} if token else {}
                request_headers.update(kwargs.pop('headers',{}))
                r=await c.request(method,path,headers=request_headers,**kwargs)
                statuses[label,str(r.status_code)]+=1
                if r.status_code < 500 and r.headers.get('x-lanshare-release') != h['release_id']:
                    raise RuntimeError('Response release identity changed during the run')
                return r
            except Exception as exc:
                statuses[label,type(exc).__name__]+=1
                if len(faults)<30:faults.append({'label':label,'error_type':type(exc).__name__})
                return None
            finally:stats[label].append((time.perf_counter()-beg)*1000);active-=1
        async def login(item):
            r=await req('login_burst','POST','/api/student/login/password',data={'identifier':item['identifier'],'password':h['synthetic_password'],'next':'/dashboard'})
            if r is None or r.status_code!=200 or not r.cookies.get('access_token'):return False
            token=r.cookies['access_token'];claim=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
            assert claim['role']=='student' and int(claim['id'])==item['student_id']
            tokens[item['student_id']]=token;logins[item['student_id']]=claim['login_time'];return True
        before=time.time(); ok=await asyncio.gather(*(login(item) for item in h['users']))
        phases.append({'name':'login_burst','users':200,'successful':sum(ok),'began_epoch':before,'ended_epoch':time.time()})
        (out/'login-summary.json').write_text(json.dumps(phases[-1]))
        print(json.dumps({'phase':'login_burst','success':sum(ok),'latency':dist(stats['login_burst'])}),flush=True)
        assert all(ok),'Synthetic login burst failed; no benchmark traffic follows.'
        c.cookies.clear();await asyncio.sleep(2)
        async def ws_connect(item,path,label):
            async with ws_slots:
                conn=await websockets.connect(args.base.replace('https:','wss:')+path,ssl=ctx,additional_headers={'Cookie':f"access_token={tokens[item['student_id']]}"},
                    open_timeout=30,close_timeout=2,max_queue=32,ping_interval=20,proxy=None)
                ready=asyncio.Event();ws_connections.append(conn)
                async def consume():
                    try:
                        async for message in conn:
                            ws_counts[label+'_frames']+=1
                            data=json.loads(message)
                            if not ready.is_set() and data.get('type')==('history' if label=='classroom' else 'hello'):
                                ready.set();ws_counts[label+'_ready']+=1
                    except Exception as exc:
                        ws_counts[('planned_' if ws_planned else 'unexpected_')+label+'_error_'+type(exc).__name__]+=1
                    finally:
                        if not ws_stopping:ws_counts[('planned_' if ws_planned else 'unexpected_')+label+'_closed']+=1
                ws_tasks.append(asyncio.create_task(consume()))
                await asyncio.wait_for(ready.wait(),30)
        async def open_all_ws():
            nonlocal ws_expected_open
            began=time.perf_counter()
            paths=[(f"/ws/{h['offering_id']}",'classroom'),('/ws/social','social')]
            if args.ws_connections==800:paths += [(f"/ws/{companion['second_offering_id']}",'classroom'),('/ws/social','social')]
            await asyncio.gather(*(ws_connect(item,path,label) for item in h['users'] for path,label in paths))
            ws_expected_open=args.ws_connections
            assert open_ws_count()==args.ws_connections,'Not all requested WebSockets remain open after readiness'
            print(json.dumps({'phase':'websocket_ready','actual_open':open_ws_count(),'connections':args.ws_connections,'seconds':time.perf_counter()-began,'counts':dict(ws_counts)}),flush=True)
        html_sizes=[]
        async def open_classroom(item,label='classroom_open_burst',identity=False):
            r=await req(label,'GET',f"/classroom/{h['offering_id']}",tokens[item['student_id']],**({'headers':{'Accept-Encoding':'identity','Cookie':f"access_token={tokens[item['student_id']]}"}} if identity else {}))
            if r is not None:html_sizes.append({'flow':label,'decoded_bytes':len(r.content),'content_encoding':r.headers.get('content-encoding'),'content_length':r.headers.get('content-length')})
            return r is not None and r.status_code==200 and 'data-classroom-page-app' in r.text and 'data-lanshare-island="classroom-page"' in r.text
        began=time.time();opened=await asyncio.gather(*(open_classroom(item) for item in h['users']))
        phases.append({'name':'classroom_open_burst','users':200,'successful':sum(opened),'began_epoch':began,'ended_epoch':time.time()})
        print(json.dumps({'phase':'classroom_open_burst','success':sum(opened),'latency':dist(stats['classroom_open_burst'])}),flush=True)
        assert all(opened),'A synthetic student could not open the real classroom HTML'
        opened_identity=await asyncio.gather(*(open_classroom(item,'classroom_open_identity_warm',True) for item in h['users']))
        assert all(opened_identity),'Identity-encoding diagnostic classroom wave failed'
        print(json.dumps({'phase':'classroom_open_identity_warm','success':sum(opened_identity),'latency':dist(stats['classroom_open_identity_warm']),'scope':'Second wave cache warm; default encoding wave remains primary'}),flush=True)
        await open_all_ws()

        async def batch(student,phase,tab=0,heartbeat_only=False,interval_floor=None):
            observed=int(time.time()*1000)-1000
            payload={'presence_version':2,'presence_observed_ms':observed,'interval_start_ms':max(observed-(60000 if heartbeat_only else 1000),interval_floor or 0),
                     'interval_end_ms':observed,'visibility_state':'visible','focused':True,'idle_seconds':0,'ai_panel_open':False}
            if payload['interval_start_ms']>=observed:
                payload.pop('interval_start_ms');payload.pop('interval_end_ms')
            events=[{'action_type':'presence_heartbeat','summary_text':'','payload':payload}]
            if not heartbeat_only:events.insert(0,{'action_type':'page_action','summary_text':'recorder load '+run,'payload':{'recorder_run':run,'tab':tab}})
            body={'batch_id':f'b1_{int(time.time()*1000)}_{uuid.uuid4()}','user_key':f'student:{student}',
                  'page_key':'rcap_'+run[:8]+'_'+phase,'events':events}
            item={'student_id':student,'phase':phase,'body':body,'login_time':logins[student],'ack':False,'attempts':0,'first_epoch':time.time()};ledger.append(item)
            for attempt in range(7):
                item['attempts']+=1
                r=await req(phase+'_recorder','POST',f"/api/classrooms/{h['offering_id']}/behavior/batch",tokens[student],json=body)
                receipt=r.json() if r is not None and 'application/json' in r.headers.get('content-type','') else {}
                if r is not None and r.status_code in (200,202) and receipt.get('status')=='success' and receipt.get('batch_id')==body['batch_id'] and receipt.get('write_mode') in ('synced','durable_queued'):
                    item.update(ack=True,ack_epoch=time.time());return
                if r is not None and r.status_code in (401,403,409,413,422):break
                await asyncio.sleep(min(8,2**attempt))
            item['failed']=True
        async def read_loop(phase,end):
            index=0
            while time.perf_counter()<end:
                student=h['users'][index%200]['student_id'];index+=1
                await req(phase+'_read','GET','/api/blog/posts',tokens[student])
                if index%25==0:
                    await req(phase+'_classroom_read','GET',f"/classroom/{h['offering_id']}",tokens[student])
                    await req(phase+'_snapshot_read','GET',f"/api/classrooms/{h['offering_id']}/learning/snapshot",tokens[student])
                await asyncio.sleep(.2)
        async def live_loop(phase,end):
            while time.perf_counter()<end:
                await req(phase+'_live','GET','/api/internal/live');await asyncio.sleep(.8)
        async def steady(users,duration,phase):
            began=time.perf_counter();phase_start=int(time.time()*1000);end=began+duration;cohort=h['users'][:users]
            async def actor(item):
                await asyncio.sleep(cohort.index(item)/users*10)
                while time.perf_counter()<end:
                    tick=time.perf_counter();await batch(item['student_id'],phase)
                    await asyncio.sleep(max(0,min(end,tick+10)-time.perf_counter()))
            async def tabs(item,tab):
                await asyncio.sleep((cohort.index(item)/users*60+tab*20)%60)
                while time.perf_counter()<end:
                    tick=time.perf_counter();await batch(item['student_id'],phase,tab,True,phase_start)
                    await asyncio.sleep(max(0,min(end,tick+60)-time.perf_counter()))
            async def material(item):
                index=cohort.index(item);last_sample=phase_start;sequence=0
                await asyncio.sleep(index/users*30)
                material_id=json.loads((work/'client-handoff/capacity-reading.json').read_text())['material_id']
                while time.perf_counter()<end:
                    tick=time.perf_counter();sequence+=1
                    is_v2=users==200 and index<150
                    body={'material_id':material_id,'session_id':None,'duration_seconds':30,'active_seconds':20,'scroll_ratio':.5,'completed':False,'page_key':'material_viewer'}
                    flow=phase+('_material_v2' if is_v2 else '_material_legacy')
                    if is_v2:
                        observed=int(time.time()*1000)-1000
                        payload={'presence_version':2,'presence_observed_ms':observed,'visibility_state':'visible','focused':True,'idle_seconds':0,'ai_panel_open':False}
                        if last_sample<observed:payload.update(interval_start_ms=max(last_sample,observed-90000),interval_end_ms=observed)
                        body.update(progress_version=2,user_key=f"student:{item['student_id']}",batch_id=f'b1_{int(time.time()*1000)}_{uuid.uuid4()}',
                            page_key='rcap_'+run[:8]+'_material_v2',events=[{'action_type':'presence_heartbeat','summary_text':'','payload':payload}])
                        entry={'kind':'material_v2','student_id':item['student_id'],'phase':flow,'body':body,'login_time':logins[item['student_id']],
                               'ack':False,'attempts':0,'first_epoch':time.time()};ledger.append(entry)
                        for attempt in range(7):
                            entry['attempts']+=1
                            r=await req(flow,'POST',f"/api/classrooms/{h['offering_id']}/learning/material-progress",tokens[item['student_id']],json=body)
                            receipt=r.json() if r is not None and 'application/json' in r.headers.get('content-type','') else {}
                            if r is not None and r.status_code in (200,202) and receipt.get('status')=='success' and receipt.get('batch_id')==body['batch_id'] and receipt.get('write_mode') in ('synced','durable_queued'):
                                entry.update(ack=True,ack_epoch=time.time(),completed=receipt.get('completed'));
                                if item['student_id'] in completed_seen:assert receipt.get('completed') is True,'Material completion regressed'
                                if receipt.get('completed'):completed_seen.add(item['student_id'])
                                break
                            if r is not None and r.status_code in (401,403,409,413,422):break
                            await asyncio.sleep(min(8,2**attempt))
                        if not entry['ack']:entry['failed']=True;return
                        last_sample=observed
                        if sequence%20==0:
                            r=await req(flow+'_same_id_replay','POST',f"/api/classrooms/{h['offering_id']}/learning/material-progress",tokens[item['student_id']],json=body)
                            assert r is not None and r.status_code in (200,202) and r.json().get('batch_id')==body['batch_id']
                    else:
                        entry={'kind':'material_legacy','student_id':item['student_id'],'phase':flow,'body':body,'ack':False,'first_epoch':time.time()};materials.append(entry)
                        r=await req(flow,'POST',f"/api/classrooms/{h['offering_id']}/learning/material-progress",tokens[item['student_id']],json=body)
                        entry['ack']=r is not None and r.status_code==200 and r.json().get('status')=='success'
                    await asyncio.sleep(max(0,min(end,tick+30)-time.perf_counter()))
            print(json.dumps({'phase':phase,'users':users,'duration':duration,'started_epoch':time.time()}),flush=True)
            await asyncio.gather(*(actor(item) for item in cohort),*(tabs(item,tab) for item in cohort for tab in range(3)),
                *(material(item) for item in cohort),read_loop(phase,end),live_loop(phase,end))
            phases.append({'name':phase,'users':users,'actual_open_websockets':open_ws_count(),'duration_actual':time.perf_counter()-began,'ended_epoch':time.time()})
            print(json.dumps({'phase':phase,'latency':{kind:dist(stats[phase+'_'+kind]) for kind in ('recorder','material_v2','material_legacy','read','classroom_read','snapshot_read','live')}}),flush=True)
        await steady(100,args.steady100,'steady100')
        await steady(200,args.steady200,'steady200')
        target=h['users'][0]['student_id']
        previous=next(item for item in reversed(ledger) if item.get('kind')=='material_v2' and item['student_id']==target and 'interval_end_ms' in item['body']['events'][0]['payload'])
        async def material_contract(body,label,discard_first_ack=False):
            body['batch_id']=f'b1_{int(time.time()*1000)}_{uuid.uuid4()}'
            entry={'kind':'material_v2','student_id':target,'phase':label,'body':body,'login_time':logins[target],'ack':False,'attempts':0,'first_epoch':time.time()};ledger.append(entry)
            for attempt in range(2 if discard_first_ack else 1):
                r=await req(label,'POST',f"/api/classrooms/{h['offering_id']}/learning/material-progress",tokens[target],json=body);entry['attempts']+=1
                assert r is not None and r.status_code in (200,202) and r.json().get('status')=='success' and r.json().get('batch_id')==body['batch_id']
                receipt=r.json()
                if discard_first_ack and attempt==0:continue
                entry.update(ack=True,ack_epoch=time.time(),completed=receipt.get('completed'))
            if target in completed_seen:assert receipt.get('completed') is True,'Completed material regressed on an old/empty replay'
            material_contracts.append({'contract':label,'passed':True,'discarded_first_ack':discard_first_ack})
        for visible,focused,label in ((True,True,'contract_visible_overlap'),(False,False,'contract_hidden_overlap')):
            body=copy.deepcopy(previous['body']);payload=body['events'][0]['payload'];payload.update(interval_start_ms=payload['interval_end_ms']-60000,visibility_state='visible' if visible else 'hidden',focused=focused)
            await material_contract(body,label,discard_first_ack=True)
        assert target in completed_seen,'The sustained material reader never reached completion'
        body=copy.deepcopy(previous['body']);payload=body['events'][0]['payload'];payload.pop('interval_start_ms',None);payload.pop('interval_end_ms',None);payload['presence_observed_ms']=int(time.time()*1000)-1000;body.update(scroll_ratio=0,completed=False)
        await material_contract(body,'contract_completed_status_only')
        for round_index in range(1,4):
            phase='burst600_r'+str(round_index);began=time.time()
            await asyncio.gather(*(batch(item['student_id'],phase,tab) for item in h['users'] for tab in range(3)))
            phases.append({'name':phase,'users':200,'batches':600,'began_epoch':began,'ended_epoch':time.time()})
            print(json.dumps({'phase':phase,'latency':dist(stats[phase+'_recorder'])}),flush=True)
            await asyncio.sleep(15)
        if args.ws_connections==800:
            prior_count=len(ws_connections)
            prior_tasks=len(ws_tasks)
            ws_expected_open=0
            await asyncio.gather(*(ws_connect(item,'/ws/social','social') for item in h['users']))
            ws_expected_open=1000
            assert open_ws_count()==1000,'Headroom phase did not retain 1000 real open WebSockets'
            end=time.perf_counter()+20
            await asyncio.gather(*(batch(item['student_id'],'headroom1000',tab) for item in h['users'] for tab in range(3)),read_loop('headroom1000',end),live_loop('headroom1000',end))
            phases.append({'name':'headroom1000','resident_connections':1000,'actual_open':open_ws_count(),'seconds':20,'ended_epoch':time.time()})
            ws_planned=True
            await asyncio.gather(*(conn.close() for conn in ws_connections[prior_count:]),return_exceptions=True)
            await asyncio.gather(*ws_tasks[prior_tasks:],return_exceptions=True)
            del ws_connections[prior_count:];del ws_tasks[prior_tasks:];ws_expected_open=800;ws_planned=False
            print(json.dumps({'phase':'headroom1000','latency':dist(stats['headroom1000_recorder']),'counts':dict(ws_counts)}),flush=True)
        ws_planned=True
        (work/'fault-ready').write_text(run)
        wait_until=time.perf_counter()+90
        while not (work/'fault-held').exists() and time.perf_counter()<wait_until:await asyncio.sleep(.1)
        assert (work/'fault-held').exists(),'Independent apply-lock fault was not injected'
        began=time.time();read_end=time.perf_counter()+45
        readers=[asyncio.create_task(read_loop('blocked_apply',read_end)),asyncio.create_task(live_loop('blocked_apply',read_end))]
        await asyncio.gather(*(batch(item['student_id'],'blocked_apply') for item in h['users']))
        phases.append({'name':'blocked_apply','users':200,'began_epoch':began,'ended_epoch':time.time()})
        (work/'fault-acked').write_text(run)
        print(json.dumps({'phase':'blocked_apply','latency':dist(stats['blocked_apply_recorder'])}),flush=True)
        wait_until=time.perf_counter()+90
        while not (work/'fault-restarted').exists() and time.perf_counter()<wait_until:await asyncio.sleep(.1)
        assert (work/'fault-restarted').exists(),'Planned durable-queue restart was not completed'
        replay=[item for item in ledger if item['phase']=='blocked_apply']
        async def replay_one(item):
            r=await req('same_id_replay','POST',f"/api/classrooms/{h['offering_id']}/behavior/batch",tokens[item['student_id']],json=item['body'])
            assert r is not None and r.status_code in (200,202) and r.json().get('batch_id')==item['body']['batch_id']
        await asyncio.gather(*(replay_one(item) for item in replay));await asyncio.gather(*readers)
        phases.append({'name':'same_id_replay_after_planned_restart','batches':len(replay),'ended_epoch':time.time()})
        await asyncio.sleep(15)
        await asyncio.gather(*(conn.close() for conn in ws_connections),return_exceptions=True)
        await asyncio.gather(*ws_tasks,return_exceptions=True)
        ws_connections.clear();ws_tasks.clear();ws_expected_open=0;ws_planned=False
        await open_all_ws();await asyncio.sleep(5)
        ws_stopping=True
        await asyncio.gather(*(conn.close() for conn in ws_connections),return_exceptions=True)
        await asyncio.gather(*ws_tasks,return_exceptions=True)
    loop_stop=True;await loop_sampler
    process_end=os.times()
    report={'client_event_loop_lag':dist(loop_lag),'client_cpu_one_core_percent':100*((process_end.user-process_begin.user)+(process_end.system-process_begin.system))/(time.perf_counter()-wall_begin),'run_id':run,'source_release':h['release_id'],'users':200,'synthetic':True,'production_contacted':False,
            'phases':phases,'latency':{name:dist(values) for name,values in stats.items()},
            'statuses':[{'flow':flow,'status':status,'count':count} for (flow,status),count in sorted(statuses.items())],
            'material_contracts':material_contracts,'completed_material_students':len(completed_seen),'planned_legacy_material_requests':len(materials),'acked_legacy_material_requests':sum(bool(item.get('ack')) for item in materials),'html_sizes':html_sizes,'websocket_open_samples':ws_open_samples,'websocket_checks_passed':bool(ws_open_samples) and all(row['actual_open']==row['expected'] for row in ws_open_samples) and not any(value for key,value in ws_counts.items() if key.startswith('unexpected_')),'websockets':dict(ws_counts),'resident_websocket_connections':args.ws_connections,'ws_handshake_limit':16,'requests_sent':sent,'in_flight_peak':peak,'batch_count':len(ledger),'acked_batches':sum(item['ack'] for item in ledger),'faults':faults,'planned_faults':['15s_apply_advisory_lock','application_process_restart_with_acked_queue']}
    (out/'request-ledger.json').write_text(json.dumps({'run_id':run,'offering_id':h['offering_id'],'users':h['users'],'batches':ledger,'materials':materials},ensure_ascii=False))
    (out/'http-report.json').write_text(json.dumps(report,indent=2));(work/'latest-run').write_text(run)
    print(json.dumps({'status':'client_complete','run_id':run,'batch_count':len(ledger),'acked_batches':report['acked_batches']}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',required=True);p.add_argument('--source-root',required=True);p.add_argument('--base',required=True);p.add_argument('--lab-nonce',required=True);p.add_argument('--release-id',required=True);p.add_argument('--ws-connections',type=int,default=800);p.add_argument('--steady100',type=int,default=60);p.add_argument('--steady200',type=int,default=600)
    asyncio.run(main(p.parse_args()))
