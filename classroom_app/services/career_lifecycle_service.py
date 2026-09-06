"""Career commands, pure state reads and revision-fenced durable job adapters."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from functools import lru_cache
from .career_public_view_service import PUBLIC_VIEW_VERSION, project_network_for_public, project_personalized_advice
from typing import Any

from ..db.connection import get_configured_db_engine
from ..db.schema_career_path import ensure_career_path_schema
from .career_recommendation_service import (
    CATALOG_VERSION, SCORER_VERSION, baseline_network, load_evidence_snapshot,
    payload_hash, recommend, validate_preferences,
)
from .student_career_job_service import (
    enqueue_student_career_job, register_student_career_handler,
    public_job_state, supersede_student_career_jobs, CareerJobCapacityError,
)

from .career_rollout_service import (
    CareerRolloutLimited, ai_availability, apply_state_availability, current_policy, require_student_ai,
)

ACTIVE = {"queued", "running", "retry_wait", "result_ready"}


def _c():
    from . import career_path_service
    return career_path_service


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _session(conn, student_id: int, *, lock: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock and get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT * FROM career_student_sessions WHERE student_id = ?" + suffix,
                       (student_id,)).fetchone()
    return dict(row) if row else {}


def _load_session_row(conn, student_id: int):
    return _session(conn, student_id) or None


@lru_cache(maxsize=64)
def _validated_graph(raw_json: str, major_name: str):
    return project_network_for_public(_c()._validate_network_payload(json.loads(raw_json),major_name))


def load_major_network_row(conn, school_code: str, major_key: str, *, lock: bool = False):
    suffix = " FOR UPDATE" if lock and get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT * FROM career_major_networks WHERE school_code = ? AND major_key = ?" + suffix,
                       (school_code, major_key)).fetchone()
    return dict(row) if row else None


def _context_hash(ctx: dict[str, Any]) -> str:
    return payload_hash({k: ctx.get(k) for k in ("school_code", "major_key", "major_name", "timeline")})


def ensure_session(conn, ctx: dict[str, Any]) -> dict[str, Any]:
    """Command-only initialization; a repeated visit does not bump the revision."""
    ensure_career_path_schema(conn)
    now = _now()
    tl = ctx["timeline"]
    conn.execute("""INSERT INTO career_student_sessions
        (student_id,school_code,major_key,major_name,status,enrollment_year,graduation_year,
         program_duration_years,context_hash,created_at,updated_at)
        VALUES (?,?,?,?,'intro',?,?,?,?,?,?) ON CONFLICT(student_id) DO NOTHING""",
        (ctx["student_id"], ctx["school_code"], ctx["major_key"], ctx["major_name"],
         tl.get("enrollment_year"), tl.get("graduation_year"), tl.get("program_duration_years"),
         _context_hash(ctx), now, now))
    row = _session(conn, ctx["student_id"], lock=True)
    if row.get("context_hash") != _context_hash(ctx):
        supersede_student_career_jobs(conn, scope_type="career_student", scope_id=str(ctx["student_id"]),
                                      student_id=ctx["student_id"])
        changed_major = (row.get("major_key"), row.get("school_code")) != (ctx["major_key"], ctx["school_code"])
        # A changed major needs a fresh question set. Never apply old A1 tags to a different graph.
        conn.execute("""UPDATE career_student_sessions SET school_code=?,major_key=?,major_name=?,
            enrollment_year=?,graduation_year=?,program_duration_years=?,context_hash=?,revision=revision+1,
            personalized_json='{}',personal_job_id=NULL,input_hash='',network_version='',
            status=?,test_answers_json=?,test_result_json=?,submitted_at=?,generated_at=?,feedback_json=?,updated_at=? WHERE student_id=?""",
            (ctx["school_code"],ctx["major_key"],ctx["major_name"],tl.get("enrollment_year"),
             tl.get("graduation_year"),tl.get("program_duration_years"),_context_hash(ctx),
             "intro" if changed_major else row["status"], "[]" if changed_major else row["test_answers_json"],
             "{}" if changed_major else row["test_result_json"],None if changed_major else row["submitted_at"],
             None if changed_major else row["generated_at"],"{}" if changed_major else row["feedback_json"],now,ctx["student_id"]))
        row = _session(conn, ctx["student_id"])
    return row


def get_or_prepare_network(conn, ctx: dict[str, Any]) -> dict[str, Any]:
    """Compatibility name: this function is now strictly read-only."""
    c = _c()
    row = load_major_network_row(conn, ctx["school_code"], ctx["major_key"])
    invalid_cached=False
    if row:
        try:
            raw = c._json_loads(row.get("network_json"), {})
            if raw.get("nodes"):
                graph = _validated_graph(_json(raw), ctx["major_name"])
                return {"status": row["status"], "source": row.get("source") or "ai", "network": graph,
                        "version": f"network:{row['id']}:{row.get('revision') or 0}", "row": row}
        except (ValueError, RuntimeError, TypeError, AttributeError):
            invalid_cached=True
    # Preserve the original software engineering catalogue, but not for unknown or unrelated majors.
    seed = c._seed_network_for(ctx["major_key"]) if ctx["major_key"] == "软件工程" else None
    graph = project_network_for_public(seed or baseline_network(ctx["major_name"]))
    return {"status": "failed" if invalid_cached else (row["status"] if row else ("ready" if seed else "not_requested")),
            "source": "seed" if seed else "baseline", "network": graph,
            "version": f"{CATALOG_VERSION}:{ctx['major_key']}:{'seed' if seed else 'baseline'}", "row": row or {},
            "invalid_cached":invalid_cached}


def _network_scope(ctx: dict[str, Any]) -> str:
    return payload_hash({"school":ctx["school_code"],"major":ctx["major_key"]})


def request_network(conn, ctx: dict[str, Any], *, retry: bool = False) -> dict[str, Any]:
    c = _c()
    if not ctx["major_name"] or ctx["major_key"] == "软件工程":
        return get_or_prepare_network(conn, ctx)
    if not ai_availability(context=ctx, system=ctx.get("student_id") is None)["allowed"]:
        if retry:
            raise CareerRolloutLimited()
        return get_or_prepare_network(conn, ctx)
    now = _now()
    conn.execute("""INSERT INTO career_major_networks
        (school_code,major_key,major_name,status,source,created_at,updated_at)
        VALUES (?,?,?,'not_requested','ai',?,?) ON CONFLICT(school_code,major_key) DO NOTHING""",
        (ctx["school_code"],ctx["major_key"],ctx["major_name"],now,now))
    row = load_major_network_row(conn,ctx["school_code"],ctx["major_key"],lock=True)
    if row.get("job_id"):
        job = public_job_state(conn,row["job_id"])
        if job.get("status") in ACTIVE:
            return get_or_prepare_network(conn,ctx)
    if row["status"] != "not_requested" and not retry:
        return get_or_prepare_network(conn,ctx)
    generation = int(row.get("generation") or 0) + 1
    scope = _network_scope(ctx)
    previous = get_or_prepare_network(conn,ctx)["network"]
    try:
        job = enqueue_student_career_job(conn,task_type=c.NETWORK_GENERATE_TASK_KIND,
            dedupe_key=f"career-network:{scope}:{generation}",scope_type="career_network",scope_id=scope,
            requester_student_id=ctx.get("student_id"),
            payload={"network_id":row["id"],"generation":generation,"school_code":ctx["school_code"],
                     "major_key":ctx["major_key"],"major_name":ctx["major_name"],
                     "requested_by_student_id":ctx.get("student_id"),
                     "previous_directions":[{"direction_id":n.get("direction_id"),"name":n["name"]} for n in previous["nodes"]]},max_attempts=3)
    except CareerJobCapacityError:
        if retry:
            raise
        conn.execute("""UPDATE career_major_networks SET status='paused',error_code='capacity_unavailable',
            error_message='专业网络增强暂不可用，基础探索仍可使用',updated_at=? WHERE id=?""",(now,row["id"]))
        return get_or_prepare_network(conn,ctx)
    conn.execute("""UPDATE career_major_networks SET status='queued',generation=?,job_id=?,
        error_code='',error_message='',updated_at=? WHERE id=?""",(generation,job["id"],now,row["id"]))
    return get_or_prepare_network(conn,ctx)


def _input(ctx, session, net):
    c = _c()
    return {"student_id":ctx["student_id"],"context_hash":_context_hash(ctx),
            "test":c._json_loads(session.get("test_result_json"),{}),
            "answers_hash":payload_hash(c._json_loads(session.get("test_answers_json"),[])),
            "quiz_version":session.get("quiz_version") or c.QUIZ_VERSION,
            "evidence":c._json_loads(session.get("evidence_json"),{}),
            "preferences":c._json_loads(session.get("preferences_json"),{}),
            "feedback":c._json_loads(session.get("feedback_json"),{}),
            "network_version":net["version"],"scorer_version":SCORER_VERSION,"catalog_version":CATALOG_VERSION,
            "public_view_version":PUBLIC_VIEW_VERSION,
            "evaluation_month":_now()[:7],
            "timeline":ctx["timeline"]}


@lru_cache(maxsize=64)
def _cached_recommend(network_json: str, inputs_json: str):
    inputs = json.loads(inputs_json)
    return recommend(json.loads(network_json),test_result=inputs["test"],evidence=inputs["evidence"],
                     preferences=inputs["preferences"],feedback=inputs["feedback"],timeline=inputs["timeline"],
                     evaluation_month=inputs["evaluation_month"])


def _baseline(ctx, session, net):
    inputs = _input(ctx,session,net)
    return _cached_recommend(_json(net["network"]),_json(inputs)),payload_hash(inputs),inputs


def _input_epoch():
    return f"{SCORER_VERSION}:{CATALOG_VERSION}:{PUBLIC_VIEW_VERSION}:{_now()[:7]}"


def _persist_baseline(conn, ctx, row, net):
    baseline, input_hash, inputs = _baseline(ctx,row,net)
    if row.get("input_hash") != input_hash:
        # Updating a baseline key must not relabel an older AI explanation as
        # matching these new inputs. Fence its job and clear its publication.
        if row.get("personal_job_id"):
            supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(ctx["student_id"]),student_id=ctx["student_id"])
        conn.execute("""UPDATE career_student_sessions SET personalized_json='{}',personal_job_id=NULL,generated_at=NULL
            WHERE student_id=?""",(ctx["student_id"],))
        row.update(personalized_json="{}",personal_job_id=None,generated_at=None)
    conn.execute("""UPDATE career_student_sessions SET baseline_json=?,input_hash=?,network_version=?,input_epoch=?
        WHERE student_id=?""",(_json(baseline),input_hash,net["version"],_input_epoch(),ctx["student_id"]))
    conn.execute("""INSERT INTO career_recommendation_versions
        (student_id,input_hash,network_version,baseline_json,created_at) VALUES (?,?,?,?,?)
        ON CONFLICT(student_id,input_hash) DO NOTHING""",
        (ctx["student_id"],input_hash,net["version"],_json(baseline),_now()))
    return baseline,input_hash,inputs


def _check_revision(row, revision):
    if revision is not None and (isinstance(revision,bool) or not isinstance(revision,int)
                                 or revision != int(row.get("revision") or 0)):
        raise _c().CareerConflict(row)


def validate_answers(answers, *, mode: str, major_key: str, complete: bool):
    c = _c()
    if mode not in ("quick","full"):
        raise ValueError("未知的问卷模式")
    questions = c.get_questions(mode=mode,major_key=major_key)
    by_id = {q["id"]:q for q in questions}
    if not isinstance(answers,list) or len(answers)>len(questions):
        raise ValueError("作答数量或格式不正确")
    selected = {}
    for answer in answers:
        if not isinstance(answer,dict):
            raise ValueError("作答必须包含题目和答案")
        qid = answer.get("question_id") or answer.get("id")
        if not isinstance(qid,str) or qid not in by_id or qid in selected:
            raise ValueError("存在未知或重复题目")
        q = by_id[qid]
        value = answer.get("value")
        options = {o["value"] for o in q.get("options",[])}
        if q["kind"] == "single" and (not isinstance(value,str) or value not in options):
            raise ValueError("请选择有效选项")
        if q["kind"] == "multi":
            if (not isinstance(value,list) or not value or len(value)>q.get("max_select",len(options))
                or any(not isinstance(v,str) or v not in options for v in value) or len(set(value))!=len(value)):
                raise ValueError("多选答案数量或选项不正确")
        if q["kind"] == "scale":
            bounds = q.get("scale") or {}
            if isinstance(value,bool) or not isinstance(value,int) or not bounds.get("min",1)<=value<=bounds.get("max",5):
                raise ValueError("量表答案超出范围")
        if q["kind"] == "text" and (not isinstance(value,str) or len(value)>q.get("max_length",200)):
            raise ValueError("文字答案超过长度限制")
        selected[qid] = {"question_id":qid,"value":value}
    if complete and any(not q.get("optional") and q["id"] not in selected for q in questions):
        raise ValueError("请完成所有必答题后提交")
    return [selected[q["id"]] for q in questions if q["id"] in selected]


def save_test_progress(conn,ctx,answers,*,mode="quick",quiz_version=None,revision=None):
    c = _c()
    row = ensure_session(conn,ctx)
    _check_revision(row,revision)
    if quiz_version not in (None,c.QUIZ_VERSION):
        raise ValueError("问卷已更新，请重新加载题目")
    if row["status"] not in ("intro","testing"):
        raise c.CareerConflict(row)
    answers = validate_answers(answers,mode=mode,major_key=ctx["major_key"],complete=False)
    conn.execute("""UPDATE career_student_sessions SET status='testing',test_answers_json=?,
        quiz_mode=?,quiz_version=?,revision=revision+1,updated_at=? WHERE student_id=? AND revision=?""",
        (_json(answers),mode,c.QUIZ_VERSION,_now(),ctx["student_id"],row["revision"]))
    return {"status":"testing","saved":True,"answered":len(answers),"revision":row["revision"]+1,
            "draft_revision":row["revision"]+1,"quiz_mode":mode,"quiz_version":c.QUIZ_VERSION}


def _request_personalization(conn,ctx,row,net):
    c = _c()
    require_student_ai(conn, ctx["student_id"])
    if not row.get("submitted_at"):
        raise ValueError("请先完成兴趣问卷")
    if row.get("evidence_stale"):
        raise ValueError("简历资料已更新，请先刷新资料与推荐，再生成详细建议")
    baseline,input_hash,inputs = _persist_baseline(conn,ctx,row,net)
    if row.get("personal_job_id"):
        existing = public_job_state(conn,row["personal_job_id"],student_id=ctx["student_id"])
        if existing.get("status") in ACTIVE:
            return existing
    # Explicit re-request creates a new revision; polling can never reset this budget.
    revision = int(row["revision"])+1
    safe_ctx = {key:ctx[key] for key in ("student_id","school_code","major_name","major_key","timeline")}
    job = enqueue_student_career_job(conn,task_type=c.PERSONALIZE_TASK_KIND,
        dedupe_key=f"career-personalize:{ctx['student_id']}:{revision}:{input_hash}",student_id=ctx["student_id"],
        scope_type="career_student",scope_id=str(ctx["student_id"]),
        payload={"student_id":ctx["student_id"],"revision":revision,"input_hash":input_hash,
                 "context":safe_ctx,"network":net["network"],"network_version":net["version"],
                 "inputs":inputs,"baseline":baseline},max_attempts=3)
    conn.execute("""UPDATE career_student_sessions SET personal_job_id=?,revision=?,status='ready',
        error_code='',error_message='',updated_at=? WHERE student_id=?""",
        (job["id"],revision,_now(),ctx["student_id"]))
    return job


def save_test_and_generate(conn,ctx,answers,*,mode="quick",quiz_version=None,revision=None,enhance=False):
    c = _c()
    row = ensure_session(conn,ctx)
    _check_revision(row,revision)
    if quiz_version not in (None,c.QUIZ_VERSION):
        raise ValueError("问卷版本已更新")
    answers = validate_answers(answers,mode=mode,major_key=ctx["major_key"],complete=True)
    supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(ctx["student_id"]),student_id=ctx["student_id"])
    result = c.score_personality_answers(answers)
    evidence = load_evidence_snapshot(conn,ctx["student_id"])
    now = _now()
    conn.execute("""UPDATE career_student_sessions SET status='ready',test_answers_json=?,test_result_json=?,
        evidence_json=?,evidence_stale=0,personalized_json='{}',personal_job_id=NULL,quiz_mode=?,quiz_version=?,revision=revision+1,
        error_code='',error_message='',submitted_at=?,updated_at=? WHERE student_id=? AND revision=?""",
        (_json(answers),_json(result),_json(evidence),mode,c.QUIZ_VERSION,now,now,ctx["student_id"],row["revision"]))
    row = _session(conn,ctx["student_id"])
    net = request_network(conn,ctx)
    _persist_baseline(conn,ctx,row,net)
    if enhance and ai_availability(context=ctx)["allowed"]:
        _request_personalization(conn,ctx,row,net)
    return {"status":"ready","test_result":result,"revision":_session(conn,ctx["student_id"])["revision"],
            "state":build_state(conn,ctx["student_id"])}


def reset_session(conn,student_id:int,*,revision=None):
    row = _session(conn,student_id,lock=True)
    if not row:
        return
    _check_revision(row,revision)
    supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(student_id),student_id=student_id)
    conn.execute("""UPDATE career_student_sessions SET status='intro',test_answers_json='[]',test_result_json='{}',
        personalized_json='{}',baseline_json='{}',personal_job_id=NULL,input_hash='',error_code='',error_message='',
        submitted_at=NULL,generated_at=NULL,revision=revision+1,updated_at=? WHERE student_id=?""",(_now(),student_id))


def initialize_career(conn,student_id:int):
    ctx = _c().resolve_student_context(conn,student_id)
    if not ctx:
        return {"ok":False,"error":"student_not_found"}
    row = ensure_session(conn,ctx)
    evidence = load_evidence_snapshot(conn,student_id)
    if row.get("evidence_json") != _json(evidence) or row.get("evidence_stale"):
        supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(student_id),student_id=student_id)
        conn.execute("""UPDATE career_student_sessions SET evidence_json=?,evidence_stale=0,personalized_json='{}',personal_job_id=NULL,
            revision=revision+1,updated_at=? WHERE student_id=?""",(_json(evidence),_now(),student_id))
        row = _session(conn,student_id)
    net = request_network(conn,ctx)
    if row.get("submitted_at"):
        baseline,input_hash,_ = _baseline(ctx,row,net)
        if row.get("input_hash") != input_hash:
            _persist_baseline(conn,ctx,row,net)
    return build_state(conn,student_id)


def invalidate_career_profile(conn,student_id:int):
    """Called inside material mutations; never silently publishes an outdated profile."""
    if get_configured_db_engine()=="postgres":
        exists=conn.execute("SELECT to_regclass('career_student_sessions') AS name").fetchone()
        available=bool(exists and exists["name"])
    else:
        available=conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='career_student_sessions'").fetchone() is not None
    if not available:
        return False
    row=_session(conn,student_id,lock=True)
    if not row:
        return False
    supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(student_id),student_id=student_id)
    conn.execute("""UPDATE career_student_sessions SET evidence_stale=1,revision=revision+1,
        personal_job_id=NULL,personalized_json='{}',updated_at=? WHERE student_id=?""",(_now(),student_id))
    return True


def update_career_preferences(conn,student_id,payload,*,revision=None):
    ctx = _c().resolve_student_context(conn,student_id)
    if not ctx:
        raise ValueError("未找到学籍信息")
    row = ensure_session(conn,ctx)
    _check_revision(row,revision)
    preferences = validate_preferences(payload)
    supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(student_id),student_id=student_id)
    conn.execute("""UPDATE career_student_sessions SET preferences_json=?,revision=revision+1,
        personalized_json='{}',personal_job_id=NULL,updated_at=? WHERE student_id=?""",(_json(preferences),_now(),student_id))
    row = _session(conn,student_id)
    if row.get("submitted_at"):
        _persist_baseline(conn,ctx,row,get_or_prepare_network(conn,ctx))
    return build_state(conn,student_id)


def record_career_feedback(conn,student_id,direction_id,action,*,revision=None):
    ctx = _c().resolve_student_context(conn,student_id)
    if not ctx:
        raise ValueError("未找到学籍信息")
    row = ensure_session(conn,ctx)
    _check_revision(row,revision)
    net = get_or_prepare_network(conn,ctx)
    if action not in ("saved","dismissed","clear") or direction_id not in {
            str(n.get("direction_id") or n["tag"]) for n in net["network"]["nodes"]}:
        raise ValueError("职业方向或反馈操作不正确")
    feedback = _c()._json_loads(row.get("feedback_json"),{})
    labels = _c()._json_loads(row.get("feedback_labels_json"),{})
    if action == "clear":
        feedback.pop(direction_id,None)
        labels.pop(direction_id,None)
    else:
        feedback[direction_id]=action
        node=next(n for n in net["network"]["nodes"] if str(n.get("direction_id") or n["tag"])==direction_id)
        labels[direction_id]={"name":node["name"],"network_version":net["version"]}
    # Keep feedback bounded even after many catalogue updates.
    if len(feedback)>120:
        raise ValueError("反馈记录过多，请先清除不再需要的记录")
    supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(student_id),student_id=student_id)
    conn.execute("""UPDATE career_student_sessions SET feedback_json=?,feedback_labels_json=?,revision=revision+1,
        personalized_json='{}',personal_job_id=NULL,updated_at=? WHERE student_id=?""",(_json(feedback),_json(labels),_now(),student_id))
    if row.get("submitted_at"):
        _persist_baseline(conn,ctx,_session(conn,student_id),net)
    return build_state(conn,student_id)


def career_job_command(conn,student_id,*,target,action,job_id=None,revision=None):
    ctx = _c().resolve_student_context(conn,student_id)
    if not ctx:
        raise ValueError("未找到学籍信息")
    row = ensure_session(conn,ctx)
    _check_revision(row,revision)
    net = get_or_prepare_network(conn,ctx)
    if target not in ("network","personalization"):
        raise ValueError("未知的任务类型")
    current_id = net["row"].get("job_id") if target=="network" else row.get("personal_job_id")
    if job_id is not None and str(job_id)!=str(current_id):
        raise _c().CareerConflict(row)
    if action=="retry":
        if target=="network":
            request_network(conn,ctx,retry=True)
        else:
            _request_personalization(conn,ctx,row,net)
    elif action=="cancel":
        if target=="network":
            # Shared work benefits an entire major; an individual student cannot cancel it.
            raise ValueError("专业网络由同专业学生共享，可离开页面后继续生成")
        supersede_student_career_jobs(conn,scope_type="career_student",scope_id=str(student_id),student_id=student_id)
        conn.execute("""UPDATE career_student_sessions SET personal_job_id=NULL,revision=revision+1,
            error_code='cancelled',error_message='',updated_at=? WHERE student_id=?""",(_now(),student_id))
    return build_state(conn,student_id)


def _task_state(conn,job_id,*,student_id=None,fallback="not_requested",error_code="",shared=False):
    job = public_job_state(conn,job_id,student_id=student_id) if job_id else {}
    status = job.get("status") or fallback
    if fallback == "failed" and error_code == "task_state_mismatch":
        status = "failed"
    labels = {"not_requested":"可生成详细建议","queued":"排队中","running":"正在生成",
              "retry_wait":"等待重试","result_ready":"正在保存","succeeded":"已完成",
              "failed":"生成失败","dead_letter":"生成失败","review_required":"需要检查",
              "cancelled":"已取消","superseded":"资料已更新","ready":"已可使用","generating":"等待恢复",
              "paused":"专业网络增强暂不可用"}
    return {**job,"id":job.get("id") or job_id,"status":status,"phase_label":labels.get(status,"待检查"),
            "message":job.get("message") or ("基础职业探索仍可使用" if status in ("failed","dead_letter") else ""),
            "error_code":job.get("error_code") or error_code,
            "can_retry":status not in ACTIVE and status not in ("ready","succeeded"),
            "can_cancel":not shared and status in ACTIVE,
            "poll_after_ms":job.get("poll_after_ms") or (8000 if status in ACTIVE else 0)}


def _result_version(ctx,session,network_version):
    return payload_hash({"network":network_version,"revision":int(session.get("revision") or 0),
                         "context":_context_hash(ctx),"enhanced":session.get("generated_at"),
                         "scorer":SCORER_VERSION,"catalog":CATALOG_VERSION,"evaluation_month":_now()[:7],
                         "public_view":PUBLIC_VIEW_VERSION,
                         "personal_job_id":session.get("personal_job_id"),
                         "evidence_stale":bool(session.get("evidence_stale"))})


def _light_state(conn,ctx,known_result_version):
    """Read bounded metadata only; no graph JSON, evidence JSON or ranking work."""
    row=conn.execute("""SELECT student_id,status,revision,quiz_mode,quiz_version,submitted_at,generated_at,
        context_hash,evidence_stale,personal_job_id,network_version,input_epoch,error_code,
        CASE WHEN personalized_json NOT IN ('{}','') THEN 1 ELSE 0 END AS has_personal
        FROM career_student_sessions WHERE student_id=?""",
        (ctx["student_id"],)).fetchone()
    session=dict(row) if row else {}
    row=conn.execute("""SELECT id,status,source,revision,job_id,error_code,
        CASE WHEN network_json NOT IN ('{}','') THEN 1 ELSE 0 END AS has_network
        FROM career_major_networks WHERE school_code=? AND major_key=?""",(ctx["school_code"],ctx["major_key"])).fetchone()
    network=dict(row) if row else {}
    seed=ctx["major_key"]=="软件工程"
    version=(f"network:{network['id']}:{network.get('revision') or 0}" if network.get("has_network") else
             f"{CATALOG_VERSION}:{ctx['major_key']}:{'seed' if seed else 'baseline'}")
    result_version=_result_version(ctx,session,version)
    if result_version!=known_result_version:
        return None
    context_changed=bool(session and session.get("context_hash")!=_context_hash(ctx))
    submitted=bool(session.get("submitted_at")) and not context_changed
    network_task=_task_state(conn,network.get("job_id"),fallback=network.get("status") or ("ready" if seed else "not_requested"),
                             error_code=network.get("error_code") or "",shared=True)
    if not ctx["major_name"] or seed:
        network_task.update({"status":"ready","phase_label":"基础网络已可使用","can_retry":False})
    personal_task=_task_state(conn,session.get("personal_job_id"),student_id=ctx["student_id"],error_code=session.get("error_code") or "")
    personal_task["can_retry"]=bool(submitted and personal_task["can_retry"])
    enhanced=bool(submitted and not session.get("evidence_stale") and session.get("has_personal")
                  and session.get("network_version")==version and session.get("input_epoch")==_input_epoch())
    inputs_outdated=bool(submitted and (session.get("network_version")!=version or session.get("input_epoch")!=_input_epoch()))
    if submitted and session.get("has_personal") and not enhanced:
        personal_task.update({"result_outdated":True,"phase_label":"旧版建议需要更新","can_retry":True,
                              "message":"基础推荐已刷新，可以重新生成详细建议。"})
    return apply_state_availability(ctx, {"ok":True,"network_unchanged":True,"initialized":bool(session),"result_version":result_version,
            "network_version":version,"revision":int(session.get("revision") or 0),"draft_revision":int(session.get("revision") or 0),
            "phase":"ready" if submitted else "intro","session_status":"ready" if submitted else session.get("status","intro"),
            "quiz_mode":session.get("quiz_mode") or "quick","quiz_version":_c().QUIZ_VERSION,
            "student":{"name":ctx["name"],"address":_c().polite_address(ctx["name"],"student"),"class_name":ctx["class_name"],"college":ctx["college"]},
            "major":{"name":ctx["major_name"] or "专业待确认","key":ctx["major_key"],"id":ctx["major_id"],
                     "identity_source":ctx["major_identity_source"],"confirmed":bool(ctx["major_name"])},
            "timeline":ctx["timeline"],"network_status":network_task["status"],
            "network_source":network.get("source") if network.get("has_network") else ("seed" if seed else "baseline"),
            "network_level":"personalized" if enhanced else "base","recommendation_source":"ai" if enhanced else "baseline",
            "needs_refresh":context_changed or bool(session.get("evidence_stale")) or inputs_outdated,
            "context_changed":context_changed,"stale":context_changed or bool(session.get("evidence_stale")) or bool(session.get("has_personal") and not enhanced),
            "tasks":{"network":network_task,"personalization":personal_task},
            "poll_after_ms":8000 if any(t["status"] in ACTIVE for t in (network_task,personal_task)) else 0})


def build_state(conn,student_id:int,*,known_result_version=""):
    c = _c()
    ctx = c.resolve_student_context(conn,student_id)
    if not ctx:
        return {"ok":False,"error":"student_not_found"}
    if known_result_version:
        light=_light_state(conn,ctx,known_result_version)
        if light is not None:
            return light
    session = _session(conn,student_id)
    net = get_or_prepare_network(conn,ctx)
    context_changed = bool(session and session.get("context_hash") != _context_hash(ctx))
    submitted = bool(session.get("submitted_at")) and not context_changed
    test = c._json_loads(session.get("test_result_json"),{}) if submitted else {}
    base,input_hash,inputs = _baseline(ctx,session,net) if submitted else ({},"",{})
    personal = c._json_loads(session.get("personalized_json"),{})
    personal_current = bool(personal and submitted and not session.get("evidence_stale") and session.get("input_hash")==input_hash
                            and session.get("network_version")==net["version"])
    result = project_personalized_advice({**base,**personal} if personal_current else base)
    graph = c.apply_personalization(net["network"],result) if result else net["network"]
    network_task = _task_state(conn,net["row"].get("job_id"),fallback=net["status"],
                               error_code=net["row"].get("error_code") or "",shared=True)
    if net.get("invalid_cached"):
        network_task.update({"status":"failed","phase_label":"专业网络需要重新生成","error_code":"network_invalid",
                             "message":"已改为可用的基础探索，原网络未通过结构检查。","can_retry":True,"can_cancel":False})
    if not ctx["major_name"] or ctx["major_key"]=="软件工程":
        network_task.update({"status":"ready","phase_label":"基础网络已可使用","can_retry":False})
    personal_task = _task_state(conn,session.get("personal_job_id"),student_id=student_id,
                                error_code=session.get("error_code") or "")
    personal_task["can_retry"] = bool(submitted and personal_task["can_retry"])
    if submitted and personal and not personal_current:
        personal_task.update({"result_outdated":True,"phase_label":"旧版建议需要更新","can_retry":True,
                              "message":"基础推荐已刷新，可以重新生成详细建议。"})
    draft = c._json_loads(session.get("test_answers_json"),[]) if not submitted and not context_changed else []
    feedback = c._json_loads(session.get("feedback_json"),{})
    labels = c._json_loads(session.get("feedback_labels_json"),{})
    current_ids = {str(n.get("direction_id") or n["tag"]) for n in graph["nodes"]}
    return apply_state_availability(ctx, {"ok":True,"initialized":bool(session),"phase":"ready" if submitted else "intro",
            "session_status":"ready" if submitted else ("testing" if draft else "intro"),
            "revision":int(session.get("revision") or 0),"draft_revision":int(session.get("revision") or 0),
            "quiz_mode":session.get("quiz_mode") or "quick","quiz_version":c.QUIZ_VERSION,
            "student":{"name":ctx["name"],"address":c.polite_address(ctx["name"],"student"),
                       "class_name":ctx["class_name"],"college":ctx["college"]},
            "major":{"name":ctx["major_name"] or "专业待确认","key":ctx["major_key"],"id":ctx["major_id"],
                     "identity_source":ctx["major_identity_source"],"confirmed":bool(ctx["major_name"])},
            "timeline":ctx["timeline"],"network":graph,"network_version":net["version"],
            "result_version":_result_version(ctx,session,net["version"]),
            "network_status":network_task["status"],"network_source":net["source"],
            "network_level":"personalized" if personal_current else "base",
            "recommendation_source":"ai" if personal_current else "baseline",
            "stale":context_changed or bool(session.get("evidence_stale")) or bool(personal and not personal_current),
            "needs_refresh":context_changed or bool(session.get("evidence_stale")) or bool(submitted and session.get("input_hash")!=input_hash),
            "context_changed":context_changed,"tasks":{"network":network_task,"personalization":personal_task},
            "prep_cards":c.build_prep_cards(net["network"],result),"job_keywords":c.build_job_keywords(net["network"],result),
            "personalized":{**c._public_personalized(result),"source":"ai" if personal_current else "baseline"},
            "rankings":base.get("rankings",[]),"preferences":c._json_loads(session.get("preferences_json"),{}),
            "feedback":c._json_loads(session.get("feedback_json"),{}),
            "feedback_by_tag":{n["tag"]:c._json_loads(session.get("feedback_json"),{}).get(str(n.get("direction_id") or n["tag"]),"") for n in graph["nodes"]},
            "unmapped_feedback":[{"name":labels.get(key,{}).get("name") or "历史方向",
                                  "action":action,"network_version":labels.get(key,{}).get("network_version") or ""}
                                 for key,action in feedback.items() if key not in current_ids],
            "test_result":{k:test.get(k) for k in ("holland_code","top_dims","location_pref","location_label")},
            "draft":draft,"error_message":"","poll_after_ms":8000 if any(t["status"] in ACTIVE for t in (network_task,personal_task)) else 0})


def _network_retry_capability(job):
    # Claiming deliberately clears ai_jobs.last_error_code. Read only the
    # immediately preceding execution, using the unique attempt key; an older
    # schema failure must not turn a later timeout/rate-limit retry into a
    # longer upstream request. Missing/interrupted attempts stay on fast text.
    attempt = int(job.get("attempt_count") or 1)
    if attempt <= 1:
        return "standard"
    try:
        with _c().get_db_connection() as conn:
            previous = conn.execute(
                "SELECT error_code,status FROM ai_job_attempts WHERE job_id=? AND attempt_no=? AND stage='execute'",
                (int(job["id"]), attempt - 1),
            ).fetchone()
    except Exception as exc:
        # History lookup is an optimization, never a new reason to fail a
        # retry. Log only the bounded exception class, with no input or IDs.
        logging.getLogger(__name__).warning("Career retry history unavailable (%s); using standard", type(exc).__name__[:80])
        return "standard"
    return "thinking" if previous and previous["status"] == "error" and previous["error_code"] in {"ValueError", "TypeError"} else "standard"


async def execute_network(job,payload):
    from .career_payload_service import expand_network_candidate, GENERATION_CONTRACT
    c = _c()
    research = await c.ai_web_research.gather(objective=f"了解中国高校{payload['major_name']}专业的真实职业方向与准备要求。",
        context="用于职业探索，不生成招聘职位；无法核验的薪资或需求数字不要提供。",max_queries=2)
    system,user = c.build_network_generation_prompt(payload["major_name"],research.get("digest") or "")
    if payload.get("previous_directions"):
        names = [item["name"] for item in payload["previous_directions"][:60] if isinstance(item,dict) and isinstance(item.get("name"),str)]
        user += "\n现有方向名称供参考；相同职责沿用原名，可补充其他相关方向，不需要输出稳定标识：\n" + _json(names)
    # Pool waits and the short retry-history read run outside the event loop.
    capability = await asyncio.to_thread(_network_retry_capability, job) if int(job.get("attempt_count") or 1) > 1 else "standard"
    raw = await c._call_career_ai(system,user,label=f"career_network:{job['id']}",capability=capability,timeout=180 if capability=="thinking" else 120)
    return {"network":expand_network_candidate(raw,payload["major_name"],previous_directions=payload.get("previous_directions")),
            "sources":{"queries":research.get("queries") or [],"checked_at":_now(),"verified":False,
                       "research_used":bool(research.get("used")),"generation_contract":GENERATION_CONTRACT,
                       "model_capability":capability}}


def apply_network(conn,job,payload,result):
    from .career_payload_service import assign_network_direction_ids
    c = _c()
    row = conn.execute("SELECT * FROM career_major_networks WHERE id=?",(payload["network_id"],)).fetchone()
    if not row or int(row["generation"])!=payload["generation"] or int(row["job_id"] or 0)!=int(job["id"]):
        return False
    previous = c._json_loads(row["network_json"],{})
    if not previous.get("nodes"):
        previous = baseline_network(row["major_name"])
    candidate = assign_network_direction_ids(result["network"],row["major_name"],previous["nodes"])
    network = c._validate_network_payload(candidate,row["major_name"])
    revision = int(row["revision"])+1
    now = _now()
    conn.execute("""INSERT INTO career_network_versions(network_id,revision,network_json,sources_json,schema_version,created_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(network_id,revision) DO NOTHING""",
        (row["id"],revision,_json(network),_json(result.get("sources") or {}),c.NETWORK_SCHEMA_VERSION,now))
    cursor = conn.execute("""UPDATE career_major_networks SET status='ready',network_json=?,revision=?,schema_version=?,
        sources_json=?,error_code='',error_message='',generated_at=?,updated_at=? WHERE id=? AND generation=? AND job_id=?""",
        (_json(network),revision,c.NETWORK_SCHEMA_VERSION,_json(result.get("sources") or {}),now,now,row["id"],payload["generation"],job["id"]))
    return bool(cursor.rowcount)


def fail_network(conn,job,payload,error_code,error_message):
    conn.execute("""UPDATE career_major_networks SET status='failed',error_code=?,error_message=?,updated_at=?
        WHERE id=? AND generation=? AND job_id=?""",
        (str(error_code)[:80],str(error_message)[:400],_now(),payload["network_id"],payload["generation"],job["id"]))


async def execute_personalization(job,payload):
    c = _c()
    # Deterministic ranking is authoritative. AI only explains already-evidenced suggestions.
    system = ("你是高校职业探索顾问。只解释给定的推荐与能力证据，不改动排序、不虚构技能和经历。"
              "输出JSON对象，包含summary、timeline_advice、node_tips。不要推断心理状态或性别，不提供录用概率。"
              "只给具体可尝试的准备任务，不输出薪资、市场增长或岗位热度结论，不承诺录用、晋升年限或抗AI替代。"
              "学历、工作经验和执业资格仅按给定证据描述，缺失时提示核对实际岗位。")
    user = _json({"major":payload["context"]["major_name"],"timeline":payload["inputs"]["timeline"],
                  "preferences":payload["inputs"]["preferences"],
                  "additional_career_goals":str(payload["inputs"].get("test",{}).get("free_text") or "")[:200],
                  "recommendations":payload["baseline"]["rankings"][:8]})
    raw = await c._call_career_ai(system,user,label=f"career_personalize:{job['id']}",timeout=180)
    cleaned = c._validate_personalization_payload(raw,payload["network"])
    if not cleaned.get("summary") and not cleaned.get("timeline_advice"):
        raise ValueError("AI 未给出有效建议")
    return {key:cleaned[key] for key in ("summary","timeline_advice","node_tips")}


def apply_personalization_result(conn,job,payload,result):
    row = _session(conn,payload["student_id"])
    ctx = _c().resolve_student_context(conn,payload["student_id"])
    if not row or not ctx or row.get("evidence_stale") or row["revision"]!=payload["revision"] or row.get("personal_job_id")!=job["id"]:
        return False
    current = get_or_prepare_network(conn,ctx)
    _,input_hash,_ = _baseline(ctx,row,current)
    if input_hash!=payload["input_hash"]:
        return False
    now = _now()
    cursor = conn.execute("""UPDATE career_student_sessions SET personalized_json=?,status='ready',error_code='',error_message='',
        generated_at=?,updated_at=? WHERE student_id=? AND revision=? AND personal_job_id=?""",
        (_json(result),now,now,payload["student_id"],payload["revision"],job["id"]))
    if cursor.rowcount:
        conn.execute("""UPDATE career_recommendation_versions SET personalized_json=?,source='ai'
            WHERE student_id=? AND input_hash=?""",(_json(result),payload["student_id"],input_hash))
    return bool(cursor.rowcount)


def fail_personalization(conn,job,payload,error_code,error_message):
    conn.execute("""UPDATE career_student_sessions SET error_code=?,error_message=?,status='ready',updated_at=?
        WHERE student_id=? AND revision=? AND personal_job_id=?""",
        (str(error_code)[:80],str(error_message)[:400],_now(),payload["student_id"],payload["revision"],job["id"]))


def recover_career_jobs(conn,limit=25):
    """Bounded maintenance; old scheduler handlers are disabled in this version."""
    c = _c()
    policy = current_policy()
    restriction, scope_args = "", []
    if not policy.valid or policy.mode != "all":
        scopes = sorted(policy.major_scopes) if policy.valid else []
        restriction = " AND (" + " OR ".join("(n.school_code=? AND n.major_key=?)" for _ in scopes) + ")" if scopes else " AND 1=0"
        scope_args = [value for scope in scopes for value in scope]
    rows = conn.execute("""SELECT n.*,j.status AS task_status FROM career_major_networks n
        LEFT JOIN ai_jobs j ON j.id=n.job_id WHERE n.status IN ('generating','queued','running')
        AND (j.id IS NULL OR j.status NOT IN ('queued','running','retry_wait','result_ready'))""" + restriction +
        " ORDER BY n.updated_at LIMIT ?", (*scope_args, limit)).fetchall()
    recovered = 0
    for row in rows:
        if row["task_status"]:
            conn.execute("""UPDATE career_major_networks SET status='failed',error_code='task_state_mismatch',
                error_message='后台任务已结束，基础探索仍可使用，请重试专业增强',updated_at=? WHERE id=? AND job_id=?""",
                (_now(),row["id"],row["job_id"]))
        else:
            ctx={"school_code":row["school_code"],"major_key":row["major_key"],"major_name":row["major_name"]}
            try:
                request_network(conn,ctx,retry=True)
            except CareerJobCapacityError:
                conn.execute("""UPDATE career_major_networks SET status='paused',error_code='capacity_unavailable',
                    updated_at=? WHERE id=?""",(_now(),row["id"]))
        recovered += 1
    # Legacy personal jobs get a usable baseline immediately; enhancements can be requested explicitly.
    rows = conn.execute("""SELECT student_id FROM career_student_sessions WHERE status IN ('generating','submitted','failed')
        AND personal_job_id IS NULL ORDER BY updated_at LIMIT ?""",(limit,)).fetchall()
    for row in rows:
        conn.execute("""UPDATE career_student_sessions SET status=CASE WHEN submitted_at IS NULL THEN 'intro' ELSE 'ready' END,
            revision=revision+1,updated_at=? WHERE student_id=? AND personal_job_id IS NULL""",(_now(),row["student_id"]))
        recovered += 1
    return {"recovered":recovered}


def restore_network_version(conn, *, school_code, major_key, revision, reason):
    """Controlled operator recovery; append a new version, never rewrite history."""
    if not isinstance(revision,int) or isinstance(revision,bool) or revision<1 or not isinstance(reason,str) or not 1<=len(reason.strip())<=500:
        raise ValueError("恢复版本与说明不正确")
    row=load_major_network_row(conn,school_code,major_key,lock=True)
    if not row:
        raise ValueError("专业网络不存在")
    old=conn.execute("SELECT * FROM career_network_versions WHERE network_id=? AND revision=?",(row["id"],revision)).fetchone()
    if not old:
        raise ValueError("可恢复的历史网络版本不存在")
    graph=_c()._validate_network_payload(json.loads(old["network_json"]),row["major_name"])
    scope=_network_scope({"school_code":school_code,"major_key":major_key})
    supersede_student_career_jobs(conn,scope_type="career_network",scope_id=scope)
    current_revision=int(row["revision"])+1;now=_now()
    sources={"restored_from_revision":revision,"reason":reason.strip(),"previous_sources":json.loads(old["sources_json"])}
    conn.execute("""INSERT INTO career_network_versions(network_id,revision,network_json,sources_json,schema_version,created_at)
        VALUES(?,?,?,?,?,?)""",(row["id"],current_revision,_json(graph),_json(sources),_c().NETWORK_SCHEMA_VERSION,now))
    conn.execute("""UPDATE career_major_networks SET network_json=?,sources_json=?,schema_version=?,revision=?,
        generation=generation+1,job_id=NULL,status='ready',error_code='',error_message='',generated_at=?,updated_at=? WHERE id=?""",
        (_json(graph),_json(sources),_c().NETWORK_SCHEMA_VERSION,current_revision,now,now,row["id"]))
    return {"network_id":row["id"],"revision":current_revision,"restored_from_revision":revision}


register_student_career_handler("career_major_network_generate",execute=execute_network,apply=apply_network,
                               fail=fail_network,timeout_seconds=360,lane="ai")
register_student_career_handler("career_personalize_generate",execute=execute_personalization,
                               apply=apply_personalization_result,fail=fail_personalization,timeout_seconds=210,lane="ai")
