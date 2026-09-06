"""Source-backed public vacancies and student-owned target handoff.

No crawler or synthetic seeding runs here. An operator must provide an identified
source and check/expiry timestamps. State reads neither write nor start AI work.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit

from ..db.connection import get_configured_db_engine
from .career_recommendation_service import payload_hash
from .resume.resume_job_target_service import analyze_job_description, create_job_target, get_job_target


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now():
    return datetime.now(timezone.utc)


def _date(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field}必须为带时区的时间")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field}时间格式不正确") from None
    if result.tzinfo is None:
        raise ValueError(f"{field}必须包含时区")
    return result.astimezone(timezone.utc).isoformat(timespec="seconds")


def validate_posting(raw, *, now=None):
    if not isinstance(raw, dict):
        raise ValueError("职位必须为对象")
    now = now or _now()
    limits = {"source": 120, "external_id": 200, "school_code": 80, "source_url": 2000,
              "title": 100, "company": 100, "city": 80, "employment_type": 60,
              "job_description": 15000, "status": 20}
    required = {"source", "external_id", "source_url", "title", "company", "city", "job_description", "status"}
    if set(raw) - set(limits) - {"published_at", "checked_at", "expires_at"}:
        raise ValueError("职位包含不支持的字段")
    item = {}
    for field, limit in limits.items():
        value = raw.get(field, "")
        if not isinstance(value, str) or len(value)>limit or any(ord(c)<32 and c not in "\n\r\t" for c in value):
            raise ValueError(f"{field}格式或长度不正确")
        item[field] = value.strip()
        if field in required and not item[field]:
            raise ValueError(f"职位缺少{field}")
    url = urlsplit(item["source_url"])
    if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
        raise ValueError("原始职位链接必须是有效的 HTTP(S) 链接")
    if len(item["job_description"])<30:
        raise ValueError("职位职责与条件至少需要30字")
    if item["status"] not in ("open", "closed", "expired"):
        raise ValueError("职位状态必须是open、closed或expired")
    item["checked_at"] = _date(raw.get("checked_at"), "checked_at")
    item["expires_at"] = _date(raw.get("expires_at"), "expires_at")
    item["published_at"] = _date(raw["published_at"], "published_at") if raw.get("published_at") else ""
    checked = datetime.fromisoformat(item["checked_at"])
    expiry = datetime.fromisoformat(item["expires_at"])
    if checked > now + timedelta(minutes=5) or expiry <= checked:
        raise ValueError("核验时间不能在未来，有效期必须晚于核验时间")
    if expiry > checked + timedelta(days=366):
        raise ValueError("职位需要至少每年重新核验有效期")
    if item["published_at"] and item["published_at"] > item["checked_at"]:
        raise ValueError("发布时间不能晚于核验时间")
    item["jd_hash"] = payload_hash(item["job_description"])
    item["content_hash"] = payload_hash({k:v for k,v in item.items() if k != "checked_at"})
    return item


def upsert_job_posting(conn, raw, *, now=None):
    """Administrative transaction; source+external_id is the durable identity."""
    item = validate_posting(raw, now=now)
    stamp = (now or _now()).isoformat(timespec="seconds")
    analysis = analyze_job_description({}, item["job_description"])
    # INSERT first makes concurrent first imports serialize on the unique key.
    fields = list(item)
    conn.execute(f"""INSERT INTO career_job_postings
        ({','.join(fields)},analysis_json,created_at,updated_at) VALUES({','.join('?' for _ in fields)},?,?,?)
        ON CONFLICT(source,external_id) DO NOTHING""", tuple(item.values()) + (_json(analysis), stamp, stamp))
    lock = " FOR UPDATE" if get_configured_db_engine() == "postgres" else ""
    row = dict(conn.execute("SELECT * FROM career_job_postings WHERE source=? AND external_id=?"+lock,
                            (item["source"],item["external_id"])).fetchone())
    version = int(row["version"]) + int(row["content_hash"] != item["content_hash"])
    conn.execute("UPDATE career_job_postings SET " + ",".join(f"{field}=?" for field in fields)
                 + ",version=?,analysis_json=?,updated_at=? WHERE id=?",
                 tuple(item.values()) + (version,_json(analysis),stamp,row["id"]))
    conn.execute("""INSERT INTO career_job_posting_versions(posting_id,version,snapshot_json,created_at)
        VALUES(?,?,?,?) ON CONFLICT(posting_id,version) DO NOTHING""",
        (row["id"],version,_json({**item,"id":row["id"],"version":version}),stamp))
    return {"id":row["id"],"version":version,"jd_hash":item["jd_hash"]}


def _bundle(conn, student_id):
    # Read a bounded profile only when the student requests a vacancy page. No
    # schema helper / biography / mental-support / contact data is consulted.
    fields = {
        "education": ("resume_educations", "school,college,major,degree,start_date,end_date,content"),
        "experience": ("resume_experiences", "kind,title,role,start_date,end_date,content,contribution,achievement"),
        "skill": ("resume_skills", "name,level,description"),
        "certificate": ("resume_certificates", "name,description,acquired_date,expiry_date"),
    }
    bundle = {}
    for section,(table,columns) in fields.items():
        rows = conn.execute(f"SELECT id,revision,{columns} FROM {table} WHERE student_id=? ORDER BY id DESC LIMIT 40",
                            (student_id,)).fetchall()
        bundle[section] = []
        for row in rows:
            item={}; remaining=2400
            for key,value in dict(row).items():
                if isinstance(value,str):
                    value=value[:min(600,remaining)];remaining-=len(value)
                item[key]=value
            bundle[section].append(item)
    return bundle


@lru_cache(maxsize=32)
def _match(student_id, bundle_json, description, today):
    # student_id prevents private result reuse across people even for equal text.
    return analyze_job_description(json.loads(bundle_json), description)


def _public(row):
    keys = ("id","title","company","city","employment_type","source","source_url","external_id",
            "published_at","checked_at","expires_at","status","version","jd_hash")
    return {key:row[key] for key in keys}


def list_job_postings(conn, student_id, *, city="", keyword="", page=1, page_size=20, qualification="all"):
    from .career_path_service import resolve_student_context
    ctx = resolve_student_context(conn,student_id)
    if not ctx:
        raise LookupError("未找到学籍信息")
    if qualification not in ("all","no_known_gaps","confirmed"):
        raise ValueError("资格筛选不正确")
    if (not isinstance(city,str) or len(city)>80 or not isinstance(keyword,str) or len(keyword)>80
            or not 1<=page<=10000 or not 1<=page_size<=20):
        raise ValueError("职位筛选或分页参数不正确")
    preferences = conn.execute("SELECT preferences_json FROM career_student_sessions WHERE student_id=?",(student_id,)).fetchone()
    preferences = json.loads(preferences["preferences_json"]) if preferences else {}
    cities = [city.strip()] if city.strip() else (preferences.get("cities") or ([preferences["city"]] if preferences.get("city") else []))
    cities = [x for x in cities[:8] if isinstance(x,str) and x]
    filters = ["status='open'", "expires_at>?", "checked_at<=?", "source<>''", "external_id<>''", "source_url<>''",
               "(school_code='' OR school_code=?)"]
    now = _now().isoformat(timespec="seconds")
    args = [now,now,ctx["school_code"]]
    if cities:
        filters.append("city IN ("+",".join("?" for _ in cities)+")");args.extend(cities)
    if keyword.strip():
        filters.append("(LOWER(title) LIKE LOWER(?) ESCAPE '\\' OR LOWER(company) LIKE LOWER(?) ESCAPE '\\')")
        pattern="%"+keyword.strip().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
        args.extend((pattern,pattern))
    where = " AND ".join(filters)
    total = conn.execute("SELECT COUNT(*) AS count FROM career_job_postings WHERE "+where,args).fetchone()["count"]
    rows = conn.execute("""SELECT id,title,company,city,employment_type,source,source_url,external_id,
        published_at,checked_at,expires_at,status,version,jd_hash,job_description FROM career_job_postings WHERE """
                        +where+" ORDER BY checked_at DESC,id DESC LIMIT ? OFFSET ?",
                        (*args,page_size,(page-1)*page_size)).fetchall()
    items=[];filtered=0
    bundle_json = _json(_bundle(conn,student_id)) if rows else "{}"
    for raw in rows:
        row=dict(raw);analysis=_match(student_id,bundle_json,row["job_description"],now[:10])
        checks=analysis.get("hard_requirements") or []
        required=[check for check in checks if check.get("importance")=="required"]
        failed=any(check["state"]=="failed" for check in required)
        extraction_complete=all(check.get("extraction_complete") is not False for check in required)
        confirmed=bool(required) and extraction_complete and all(check["state"]=="met" for check in required)
        if (qualification=="no_known_gaps" and failed) or (qualification=="confirmed" and not confirmed):
            filtered+=1;continue
        items.append({**_public(row),"match":{
            "hard_requirements":checks,"capabilities":analysis.get("capabilities",[]),
            "coverage_score":analysis.get("coverage_score"),"coverage_status":analysis.get("coverage_status"),
            "requirements_extraction_complete":extraction_complete,
            "qualification_state":"known_gap" if failed else ("self_reported_met" if confirmed else "unknown"),
            "summary":"存在已知硬条件差距" if failed else ("自述资料覆盖已提取条件，仍需招聘方核验" if confirmed else "部分条件尚待核实，不代表不符合"),
            "analysis_version":analysis.get("analysis_version")}})
    visible_sources = conn.execute("""SELECT COUNT(*) AS count FROM career_job_postings
        WHERE source<>'' AND external_id<>'' AND source_url<>'' AND (school_code='' OR school_code=?)""",
                                   (ctx["school_code"],)).fetchone()["count"] if not items else 1
    return {"ok":True,"items":items,"page":page,"page_size":page_size,"total":total,
            "total_scope":"open_city_candidates_before_qualification","filtered_on_page":filtered,
            "has_more":page*page_size<total,"cities":cities,"keyword":keyword.strip(),"qualification":qualification,
            "empty_reason":("no_verified_source" if not visible_sources else "no_matching_open_jobs") if not items else ""}


def create_posting_target(conn, student_id, posting_id):
    from .career_path_service import resolve_student_context
    ctx=resolve_student_context(conn,student_id)
    if not ctx:
        raise LookupError("未找到学籍信息")
    lock=" FOR UPDATE" if get_configured_db_engine()=="postgres" else ""
    # Student lock serializes a double click without holding a shared posting lock.
    conn.execute("SELECT id FROM students WHERE id=?"+lock,(student_id,)).fetchone()
    now=_now().isoformat(timespec="seconds")
    raw=conn.execute("""SELECT * FROM career_job_postings WHERE id=? AND status='open' AND expires_at>?
        AND checked_at<=? AND source<>'' AND source_url<>'' AND (school_code='' OR school_code=?)""",
        (posting_id,now,now,ctx["school_code"])).fetchone()
    if not raw:
        raise LookupError("该职位已结束、尚未核验或不在当前可见范围")
    posting=dict(raw)
    existing=conn.execute("""SELECT job_target_id FROM career_posting_targets
        WHERE student_id=? AND posting_id=? AND posting_version=?""",(student_id,posting_id,posting["version"])).fetchone()
    if existing:
        target=get_job_target(conn,student_id,existing["job_target_id"])
        if not target.get("archived"):
            return {"ok":True,"item":target,"job_target_id":target["id"]}
    target=create_job_target(conn,student_id,target_position=posting["title"],company_name=posting["company"],
                             job_description=posting["job_description"])
    conn.execute("""INSERT INTO career_posting_targets(student_id,posting_id,posting_version,job_target_id,source_url,created_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(student_id,posting_id,posting_version) DO UPDATE SET job_target_id=excluded.job_target_id""",
        (student_id,posting_id,posting["version"],target["id"],posting["source_url"],now))
    analysis=target.get("analysis") or {}
    analysis={**analysis,"posting_source":{**_public(posting),"kind":"verified_source_posting"}}
    conn.execute("UPDATE resume_job_targets SET analysis_json=? WHERE id=? AND student_id=?",(_json(analysis),target["id"],student_id))
    target["analysis"]=analysis
    return {"ok":True,"item":target,"job_target_id":target["id"]}
