"""Keep attendance histories and remote intentions coherent across course lifecycle."""
from __future__ import annotations

from fastapi import HTTPException
from ..db.connection import get_configured_db_engine
from . import login_attendance_service as attendance


def _exists(conn, name):
    if get_configured_db_engine()=="postgres":
        return bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?",(name,)).fetchone())
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


def blockers(conn, offering_ids, *, merging):
    ids=tuple(int(x) for x in offering_ids)
    if not ids:return []
    marks=','.join('?' for _ in ids)
    messages=[]
    if _exists(conn,'login_attendance_rounds') and merging:
        active=conn.execute(f"SELECT COUNT(*) AS n FROM login_attendance_rounds WHERE class_offering_id IN ({marks}) "
            "AND state IN ('scheduled','running','settling') AND is_current=1",ids).fetchone()['n']
        if active:messages.append(f"有 {active} 轮本地点名尚未结束结算，请先结束并等待结算或作废后合班。")
        if _exists(conn,'approval_requests'):
            pending=conn.execute(f"SELECT COUNT(*) AS n FROM approval_requests WHERE class_offering_id IN ({marks}) "
                "AND request_type='login_attendance_appeal' AND status='pending'",ids).fetchone()['n']
            if pending:messages.append(f"有 {pending} 条签到申诉尚待处理，请先完成核对再合班，历史处理记录会保留。")
    if _exists(conn,'smart_attendance_write_bindings'):
        pending=conn.execute(f"SELECT COUNT(*) AS n FROM smart_attendance_sync_runs r JOIN smart_attendance_write_bindings b ON b.id=r.binding_id "
            f"WHERE b.class_offering_id IN ({marks}) AND r.state IN ('pending','outcome_unknown','needs_teacher_reauth')",ids).fetchone()['n']
        from .smart_classroom_write_service import _now
        leases=conn.execute(f"SELECT COUNT(*) AS n FROM smart_attendance_write_bindings b JOIN smart_attendance_account_leases l "
            f"ON l.external_account_key=b.external_account_key WHERE b.class_offering_id IN ({marks}) AND l.lease_until>?",(*ids,_now())).fetchone()['n']
        if pending or leases:messages.append("智慧课堂仍有正在处理或结果待核实的同步，请先完成远端回读核对，再合班或删除课堂。")
    return messages


def _pause_remote(conn, offering_ids, *, target_id=None):
    if not _exists(conn,'smart_attendance_write_bindings'):return 0
    marks=','.join('?' for _ in offering_ids)
    bindings=conn.execute(f"SELECT id FROM smart_attendance_write_bindings WHERE class_offering_id IN ({marks})",tuple(offering_ids)).fetchall()
    from .smart_classroom_write_service import _now
    from .scheduled_task_service import cancel_tasks_by_dedupe
    stamp=_now()
    for binding in bindings:
        runs=conn.execute("SELECT id,state FROM smart_attendance_sync_runs WHERE binding_id=?",(binding['id'],)).fetchall()
        for run in runs:
            if _exists(conn,'scheduled_tasks'):
                cancel_tasks_by_dedupe(conn,'smart-attendance-readback:'+run['id'])
            if run['state'] not in {'confirmed','superseded'}:
                conn.execute("UPDATE smart_attendance_sync_runs SET state='paused',revision=revision+1,message=?,updated_at=? WHERE id=?",
                    ('课堂归档，停止后续同步；既有远端事实与审计保留。',stamp,run['id']))
        conn.execute("UPDATE smart_attendance_write_bindings SET state='paused',session_id=NULL,revision=revision+1,updated_at=? "
                     +(" ,class_offering_id=?" if target_id is not None else "")+" WHERE id=?",
                     (stamp,target_id,binding['id']) if target_id is not None else (stamp,binding['id']))
    return len(bindings)


def _archive_appeal_links(conn,round_id,*,target_id=None):
    if not _exists(conn,'approval_requests'):return
    rows=conn.execute("SELECT a.id,a.payload_json FROM approval_requests a JOIN login_attendance_records r "
        "ON a.subject_id=CAST(r.id AS TEXT) WHERE a.request_type='login_attendance_appeal' AND r.round_id=?",(round_id,)).fetchall()
    for request in rows:
        payload=attendance._load(request['payload_json'])
        payload.update(history_only=True,session_id=None,deleted_archive=target_id is None)
        conn.execute('UPDATE approval_requests SET payload_json=? WHERE id=?',(attendance._json(payload),request['id']))
        if not _exists(conn,'message_center_notifications'):continue
        teacher_link=(f"/classroom/{target_id}?attendance_id={round_id}&history_only=1&student_id={payload.get('student_id')}&appeal_id={request['id']}"
                      if target_id is not None else '/profile?section=notifications#profile-message-center')
        for role,link in (('teacher',teacher_link),('student','/profile?section=notifications#profile-message-center')):
            conn.execute("UPDATE message_center_notifications SET link_url=? WHERE recipient_role=? AND ref_type='approval_workflow' AND ref_id LIKE ?",
                         (link,role,f"login_attendance_appeal:{request['id']}:%"))


def archive_for_merge(conn, target_id, source_ids, teacher_id):
    """Move histories by stable round/student IDs; never remap school slots by order."""
    if not _exists(conn,'login_attendance_rounds'):return 0
    messages=blockers(conn,[target_id,*source_ids],merging=True)
    if messages:raise HTTPException(409,'；'.join(messages))
    user={'role':'teacher','id':teacher_id}
    marks=','.join('?' for _ in source_ids)
    rows=conn.execute(f"SELECT * FROM login_attendance_rounds WHERE class_offering_id IN ({marks}) ORDER BY id",tuple(source_ids)).fetchall()
    for raw in rows:
        row=dict(raw);context=attendance._load(row['context_json'])
        context.setdefault('merged_from',{'class_offering_id':row['class_offering_id'],'session_id':row['session_id'],
                                         'was_current':bool(row['is_current'])})
        context['merged_into_offering_id']=target_id
        context['history_only']=True
        conn.execute("UPDATE login_attendance_rounds SET class_offering_id=?,session_id=NULL,is_current=0,teacher_id=?,"
            "context_json=?,request_key=?,revision=revision+1 WHERE id=?",
            (target_id,teacher_id,attendance._json(context),f"merged-round-{row['id']}",row['id']))
        attendance._audit(conn,row['id'],'classroom_merged',user,before={'class_offering_id':row['class_offering_id'],'session_id':row['session_id']},
            after={'class_offering_id':target_id,'session_id':None},reason='历史签到独立归档，原课次时间与冻结名单保留。')
        _archive_appeal_links(conn,row['id'],target_id=target_id)
    _pause_remote(conn,source_ids,target_id=target_id)
    return len(rows)


def prepare_deletion(conn, offering_id, user):
    attendance.require_teacher(conn,offering_id,user,lock=True)
    messages=blockers(conn,[offering_id],merging=False)
    if messages:raise HTTPException(409,'；'.join(messages))
    if not _exists(conn,'login_attendance_rounds'):return
    rows=conn.execute('SELECT * FROM login_attendance_rounds WHERE class_offering_id=? ORDER BY id',(offering_id,)).fetchall()
    for raw in rows:
        row=dict(raw)
        _archive_appeal_links(conn,row['id'])
        if row['state'] in {'scheduled','running','settling'}:
            attendance.void_round(conn,row['id'],user,{'revision':row['revision'],'reason':'课堂已删除，停止本轮统计并保留历史。'})
        else:
            attendance._cancel_appeals(conn,row['id'],None,user,'课堂已归档，本次申请结束；历史证据保留。')
        context=attendance._load(row['context_json'])
        context['deleted_from']={'class_offering_id':offering_id,'session_id':row['session_id']}
        context['history_only']=True
        conn.execute('UPDATE login_attendance_rounds SET context_json=?,teacher_id=?,is_current=0,revision=revision+1 WHERE id=?',
            (attendance._json(context),user['id'],row['id']))
        attendance._audit(conn,row['id'],'classroom_deleted',user,reason='保留历史，结束本地待办并暂停远端同步。')
    _pause_remote(conn,[offering_id])
