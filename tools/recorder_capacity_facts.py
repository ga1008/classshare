"""Raw PG measurements inside the explicitly owned empty-seeded capacity lab."""
import argparse,collections,json,time,sys,urllib.request,hashlib
if __package__ in (None, ""):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from datetime import datetime,timezone,timedelta
from pathlib import Path
from tools.platform_capacity_linux_lab import validate_container_boundary,_checked_pg_connection

def ledger_fully_acknowledged(ledger):
    batches=ledger['batches']
    return (len({item['body']['batch_id'] for item in batches})==len(batches)
            and all(item.get('ack') for item in batches)
            and all(item.get('ack') for item in ledger.get('materials',[])))

def merged_ms(entries):
    result=[]
    for start,end in sorted(entries):
        if result and start<=result[-1][1]:result[-1][1]=max(end,result[-1][1])
        else:result.append([start,end])
    return sum(e-s for s,e in result)
def ms(text):
    stamp=datetime.fromisoformat(text)
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone(timedelta(hours=8)))
    return int(stamp.timestamp()*1000)
def main(a):
    identity=validate_container_boundary();root=Path(identity['artifact_root']);
    if a.mode == 'facts':
        ledger_path=Path(a.ledger).resolve(strict=True)
        if not ledger_path.is_relative_to(root.resolve()) or ledger_path.suffix != '.json':
            raise ValueError('Client facts ledger must remain inside the owned artifact directory')
    conn,_=_checked_pg_connection(identity['marker'])
    conn.execute("SET application_name='recorder_capacity_observer'")
    try:
        if a.mode=='hold':
            conn.execute("SET application_name='recorder_capacity_fault'")
            conn.execute('SELECT pg_advisory_lock(%s,%s)',(0x42485652,2))
            (root/'apply-held').write_text(str(time.time()));print('apply lock acquired',flush=True)
            time.sleep(a.seconds)
            conn.execute('SELECT pg_advisory_unlock(%s,%s)',(0x42485652,2))
            (root/'apply-released').write_text(str(time.time()));print('apply lock released',flush=True);return
        if a.mode=='monitor':
            path=root/'recorder-pg-observations.jsonl';deadline=time.monotonic()+a.seconds
            with path.open('x') as out:
                iteration=0
                while time.monotonic()<deadline and not (root/'stop-recorder-monitor').exists():
                    iteration+=1
                    health=None
                    if iteration%5==1:
                        try:
                            payload=json.loads(urllib.request.urlopen('http://app:8000/api/internal/health',timeout=3).read())
                            health={'behavior_write_pipeline':payload.get('behavior_write_pipeline'),'db_pool':payload.get('db_pool')}
                        except Exception as exc: health={'error_type':type(exc).__name__}
                    begun=time.perf_counter()
                    states=conn.execute('SELECT state,COUNT(*),MIN(received_at) FROM behavior_write_inbox GROUP BY state').fetchall()
                    waits=conn.execute("SELECT application_name,wait_event_type,wait_event,state,COUNT(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() GROUP BY application_name,wait_event_type,wait_event,state").fetchall()
                    locks=conn.execute("SELECT classid,objid,granted,COUNT(*) FROM pg_locks WHERE locktype='advisory' AND classid=%s GROUP BY classid,objid,granted",(0x42485652,)).fetchall()
                    dirty=conn.execute('SELECT COUNT(*),COUNT(*) FILTER (WHERE dirty=1),COUNT(*) FILTER (WHERE dirty_version<>calculated_version) FROM learning_progress_snapshots WHERE class_offering_id=%s',(identity['fixture']['classOfferingId'],)).fetchone()
                    out.write(json.dumps({'epoch':time.time(),'states':states,'waits':waits,'advisory_locks':locks,'snapshots':dirty,'observer_ms':(time.perf_counter()-begun)*1000,'health':health})+'\n');out.flush();time.sleep(1)
            print(json.dumps({'status':'monitor_stopped','path':str(path)}));return
        conn.execute('SET default_transaction_read_only=on')
        ledger=json.loads(Path(a.ledger).read_text());assert ledger['offering_id']==identity['fixture']['classOfferingId']
        students={item['student_id'] for item in identity['fixture']['users']};assert {u['student_id'] for u in ledger['users']}==students
        items={i['body']['batch_id']:i for i in ledger['batches']};ids=list(items)
        rows=conn.execute('SELECT batch_id,user_pk,class_offering_id,state,received_at,applied_at,payload_hash FROM behavior_write_inbox WHERE batch_id=ANY(%s)',(ids,)).fetchall()
        actual={r[0]:r for r in rows};ranges=collections.defaultdict(list);latencies=[];expected_ops=collections.Counter();bad=[];phase_latencies=collections.defaultdict(list);material_ranges=collections.defaultdict(list);material_active_ranges=collections.defaultdict(list);payload_mismatches=[]
        for batch,item in items.items():
            row=actual.get(batch)
            if not row or row[3]!='applied':bad.append({'batch_id':batch,'state':row[3] if row else 'missing'});continue
            assert row[1]==item['student_id'] and row[2]==ledger['offering_id']
            wire=json.loads(json.dumps(item['body']['events']))
            if item.get('kind')=='material_v2':
                context={key:item['body'].get(key) for key in ('material_id','session_id','scroll_ratio','completed')};context['scroll_ratio']=float(context['scroll_ratio'])
                wire[0]['payload']={**wire[0].get('payload',{}),'material_progress_context':context}
            else:
                wire=[{'action_type':event.get('action_type','page_action'),'summary_text':event.get('summary_text',''),'page_key':event.get('page_key'),'payload':event.get('payload',{})} for event in wire]
            expected_hash=hashlib.sha256(json.dumps({'events':wire,'page_key':item['body'].get('page_key')},ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
            if expected_hash!=row[6]:payload_mismatches.append(batch)
            received=ms(row[4]);login=ms(item['login_time']);latencies.append(ms(row[5])-received);phase_latencies[item['phase']].append(ms(row[5])-received)
            for event in item['body']['events']:
                if event['action_type']=='page_action':expected_ops[item['student_id']]+=1
                p=event['payload']
                if p.get('presence_version')==2 and 'interval_start_ms' in p:
                    start,end=max(p['interval_start_ms'],login),min(p['interval_end_ms'],received)
                    if start<end:
                        if item.get('kind')=='material_v2':material_ranges[item['student_id']].append([start,end])
                        learning_end=min(end,p['presence_observed_ms']-max(0,p.get('idle_seconds',0)-150)*1000)
                        if p.get('visibility_state')=='visible' and p.get('focused') is True and start<learning_end:
                            ranges[item['student_id']].append([start,learning_end])
                            if item.get('kind')=='material_v2':material_active_ranges[item['student_id']].append([start,learning_end])
        actual_ops=dict(conn.execute("SELECT user_pk,COUNT(*) FROM classroom_behavior_events WHERE class_offering_id=%s AND summary_text=%s GROUP BY user_pk",(ledger['offering_id'],'recorder load '+ledger['run_id'])).fetchall())
        states=conn.execute("SELECT user_pk,learning_online_seconds,presence_coverage_json FROM classroom_behavior_states WHERE class_offering_id=%s AND user_role='student' AND user_pk=ANY(%s)",(ledger['offering_id'],list(students))).fetchall()
        duration_bad=[]
        for student,seconds,coverage in states:
            expected=merged_ms(ranges[student])//1000
            if seconds!=expected:duration_bad.append({'student':student,'expected':expected,'actual':seconds})
        legacy_duration=collections.Counter();legacy_active=collections.Counter()
        for item in ledger.get('materials',[]):
            if item.get('ack'):
                legacy_duration[item['student_id']]+=item['body']['duration_seconds'];legacy_active[item['student_id']]+=item['body']['active_seconds']
        material_rows=conn.execute('SELECT student_id,accumulated_seconds,active_seconds FROM learning_material_progress WHERE class_offering_id=%s AND student_id=ANY(%s)',(ledger['offering_id'],list(students))).fetchall()
        material_bad=[]
        for student,duration,active in material_rows:
            expected_duration=legacy_duration[student]+merged_ms(material_ranges[student])//1000
            expected_active=legacy_active[student]+merged_ms(material_active_ranges[student])//1000
            if duration!=expected_duration or active!=expected_active:material_bad.append({'student':student,'expected_duration':expected_duration,'actual_duration':duration,'expected_active':expected_active,'actual_active':active})
        unresolved=conn.execute("SELECT state,COUNT(*) FROM behavior_write_inbox WHERE state IN ('pending','quarantined') GROUP BY state").fetchall()
        from math import ceil
        def distribution(values):
            values=sorted(values)
            return {'count':len(values),**{f'p{p}_ms':values[min(len(values)-1,ceil(len(values)*p/100)-1)] for p in (50,95,99)},'max_ms':max(values)} if values else {}
        latencies.sort();d={f'p{p}_ms':latencies[min(len(latencies)-1,ceil(len(latencies)*p/100)-1)] for p in (50,95,99)} if latencies else {}
        complete=ledger_fully_acknowledged(ledger) and len(states)==200 and len(material_rows)==200
        result={'status':'passed' if complete and not bad and not duration_bad and not material_bad and not payload_mismatches and actual_ops==dict(expected_ops) and not unresolved else 'failed',
                'lab_nonce':identity['lab_nonce'],'run_id':ledger['run_id'],'client_batches':len(items),'received_batches':len(rows),'acked_batches':sum(i['ack'] for i in items.values()),
                'planned_legacy_material_requests':len(ledger.get('materials',[])),
                'acked_legacy_material_requests':sum(bool(item.get('ack')) for item in ledger.get('materials',[])),
                'non_applied_batches':bad[:30],'payload_hash_mismatches':payload_mismatches[:30],'material_counter_mismatches':material_bad[:30],'material_progress_rows':len(material_rows),'operation_counts_equal':actual_ops==dict(expected_ops),'expected_operations':sum(expected_ops.values()),'actual_operations':sum(actual_ops.values()),
                'duration_mismatches':duration_bad[:30],'duration_student_count':len(states),'expected_learning_seconds':sum(merged_ms(v)//1000 for v in ranges.values()),'actual_learning_seconds':sum(r[1] or 0 for r in states),
                'receive_to_apply_latency':d,'receive_to_apply_by_phase':{phase:distribution(values) for phase,values in phase_latencies.items()},'unresolved':unresolved,'production_contacted':False}
        (root/('recorder-facts-'+ledger['run_id']+'.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result))
    finally:conn.close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['monitor','hold','facts']);p.add_argument('--seconds',type=int,default=900);p.add_argument('--ledger');main(p.parse_args())
