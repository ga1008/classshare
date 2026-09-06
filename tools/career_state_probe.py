"""Measure full/cached/metadata-only state with an isolated in-memory fixture.

This includes Python JSON serialization, but excludes HTTP, real PostgreSQL and
AI latency. No application database is opened and no student data is read.
"""
from __future__ import annotations

import copy
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ["DB_ENGINE"]="sqlite"
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from tests.test_career_lifecycle import fixture, answers_for
from classroom_app.services import career_path_service as career, career_lifecycle_service as lifecycle
from classroom_app.services.career_recommendation_service import baseline_network


def main():
    conn=fixture()
    try:
        ctx=career.resolve_student_context(conn,1);state=career.initialize_career(conn,1)
        for table,columns in (("resume_skills","name,description"),("resume_certificates","name,description"),
                              ("resume_experiences","title,content"),("resume_educations","major,content")):
            conn.executemany(f"INSERT INTO {table}(student_id,{columns}) VALUES(1,?,?)",
                             [(f"材料{i}","翻译实践与表达分析，完成课程项目。"*70) for i in range(40)])
        job=dict(conn.execute("SELECT * FROM ai_jobs").fetchone());payload=json.loads(job["payload_json"])
        graph=baseline_network("英语");first=graph["nodes"][0];graph["nodes"]=[]
        for i in range(60):
            node=copy.deepcopy(first);node.update(tag=f"N{i}",direction_id=f"probe-{i}",name=f"翻译探索方向{i}")
            node["desc"]="职业探索背景。"*75;graph["nodes"].append(node)
        lifecycle.apply_network(conn,job,payload,{"network":graph})
        conn.execute("UPDATE ai_jobs SET status='succeeded'")
        state=career.save_test_and_generate(conn,ctx,answers_for(ctx),revision=state["revision"])["state"]
        conn.commit()
        report={"database":"isolated SQLite memory","directions":60,"evidence_rows":160,"cases":[]}
        for label,iterations,known,clear in (("full_cold",20,"",True),("full_warm",100,"",False),
                                            ("metadata_poll",500,state["result_version"],False)):
            times=[];counts=[];sizes=[]
            for _ in range(iterations):
                if clear:
                    lifecycle._cached_recommend.cache_clear();lifecycle._validated_graph.cache_clear()
                sql=[];conn.set_trace_callback(sql.append);started=time.perf_counter()
                result=career.build_state(conn,1,known_result_version=known)
                encoded=json.dumps(result,ensure_ascii=False).encode("utf-8")
                times.append((time.perf_counter()-started)*1000);counts.append(len(sql));sizes.append(len(encoded))
                conn.set_trace_callback(None)
            times.sort()
            report["cases"].append({"case":label,"iterations":iterations,"sql_count":max(counts),
                                    "p50_ms":round(statistics.median(times),3),
                                    "p95_ms":round(times[int((iterations-1)*.95)],3),"response_bytes":max(sizes)})
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        conn.close()


if __name__=="__main__":
    main()
