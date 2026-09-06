"""Synthetic mixed load through real ASGI routes and an isolated, pooled local PG schema.

python -B tools/career_mixed_load_probe.py --duration 60 --rps 50 --users 1000 --seed 42 --output .codex-temp/career-load-60.json
No production services/configuration are changed. The local-only parent fixture
creates and drops a unique schema; every application connection uses that schema.
ASGI measurements exclude TCP/TLS/browser and include this process's load generator.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import math
import os
import random
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

# Windows CRT does not understand an IANA TZ after a native driver calls
# _tzset(). Keep this probe's naive scheduler clock aligned with China time;
# the application itself continues to use its named IANA timezone.
if os.name == "nt":
    os.environ["TZ"] = "CST-8"
    os.environ["APP_TIMEZONE"] = "Asia/Shanghai"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import psutil
from psycopg_pool import ConnectionPool
from classroom_app import config
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.routers import profile as profile_routes
from classroom_app.services import student_career_job_service as registry
from classroom_app.services import student_career_job_worker as worker
from classroom_app.services.career_recommendation_service import baseline_network
from classroom_app.services.resume import resume_document_service as documents
from tools.career_postgres_workflow_probe import isolated_career_postgres
from tools.career_teaching_reminder_probe import ReminderMeasurements, seed_teaching, teaching_routes

LABEL = contextvars.ContextVar("probe_label", default="setup")


def distribution(values):
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {"count": len(ordered), **{f"p{p}_ms": round(ordered[min(len(ordered)-1, math.ceil(len(ordered)*p/100)-1)], 3)
            for p in (50, 95, 99)}, "max_ms": round(ordered[-1], 3), "mean_ms": round(statistics.mean(ordered), 3)}


def source_hashes():
    files = [Path(__file__), ROOT/"classroom_app/routers/career_path.py", ROOT/"classroom_app/routers/resume_console.py",
             ROOT/"classroom_app/services/career_lifecycle_service.py", ROOT/"classroom_app/services/career_path_service.py",
             ROOT/"classroom_app/services/student_career_job_worker.py", ROOT/"classroom_app/services/student_career_job_service.py",
             ROOT/"classroom_app/services/ai_durable_job_service.py"]
    files.extend((ROOT/"classroom_app/services/resume").glob("*.py"))
    files.extend((ROOT/"classroom_app/services").glob("career_*.py"))
    files.extend((ROOT/"classroom_app/db").glob("*.py"))
    files.extend(ROOT/"classroom_app/services"/name for name in ("libreoffice_service.py","document_render_service.py","file_service.py","excel_upload_service.py","material_ai_import_service.py"))
    files.extend(ROOT/"classroom_app/routers"/name for name in ("files.py","document_renderer.py"))
    files.extend([ROOT/"classroom_app/config.py",ROOT/"classroom_app/db/postgres.py",ROOT/"classroom_app/routers/profile.py"])
    files.extend([ROOT/"tools/career_teaching_reminder_probe.py",ROOT/"classroom_app/routers/ui_parts/dashboard.py"])
    files.extend([ROOT/"tools/career_postgres_workflow_probe.py",ROOT/"tools/resume_postgres_workflow_probe.py"])
    files.extend(ROOT/"classroom_app/services"/name for name in ("student_course_schedule_service.py","dashboard_service.py",
        "dashboard_calendar_service.py","academic_service.py","scheduled_task_service.py","scheduled_task_handlers.py",
        "assignment_reminder_service.py","message_center_service.py","email_notification_service.py","offering_membership_service.py",
        "academic_course_sync_service.py","smart_classroom_schedule_sync_service.py","semester_identity_service.py"))
    return {str(file.relative_to(ROOT)):hashlib.sha256(file.read_bytes()).hexdigest() for file in files}


class Measurements:
    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "setup"
        self.requests = defaultdict(list)
        self.pool_wait = defaultdict(list)
        self.sql_time = defaultdict(list)
        self.status = defaultdict(Counter)
        self.bytes = Counter()
        self.expected_conflicts = 0
        self.unexpected = []
        self.light = Counter()
        self.resources = []
        self.backend_pids = set()
        self.current_stub = 0
        self.max_stub = 0
        self.stub_count = 0
        self.skipped = 0
        self.arrival_dispatch_lag = []
        self.timing_totals = Counter()

    def timing(self, destination, label, elapsed):
        with self.lock:
            self.timing_totals[(id(destination),label)] += 1
            # 50 RPS for an hour remains bounded, including SQL instrumentation.
            values = destination[label]
            if len(values) < 250000:
                values.append(elapsed)

    def summarize(self, destination, label):
        values = destination[label]
        total = self.timing_totals[(id(destination),label)]
        return {**distribution(values),"total_count":total,"truncated_count":max(0,total-len(values)),"sample_scope":"first_250000"}


class ResourceSampler:
    """Keep all psutil calls on one owned thread, outside request dispatch.

    Process.cpu_percent and host cpu_percent retain their per-thread baselines.
    Shutdown waits for an in-flight sample without blocking the event loop.
    """
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="career-resource-sampler")
        self._process = None
        self._backends = {}
        self._shutdown_task = None
        self.machine = {}

    async def _call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _initialize(self):
        self._process = psutil.Process()
        self._process.cpu_percent()
        psutil.cpu_percent()
        self.machine = {"logical_cpus":psutil.cpu_count(),"physical_cpus":psutil.cpu_count(logical=False),
                        "ram_gb":round(psutil.virtual_memory().total/1073741824,2)}

    async def __aenter__(self):
        try:
            await self._call(self._initialize)
            return self
        except BaseException:
            await self.aclose()
            raise

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True))
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            # Do not leave an owned sampler running after the fixture is torn down.
            await self._shutdown_task
            raise

    async def cpu_seconds(self):
        return await self._call(lambda: sum(self._process.cpu_times()[:2]))

    def _sample(self, backend_pids):
        started = time.perf_counter()
        sample = {"app_and_generator_rss_mb":round(self._process.memory_info().rss/1048576,2),
                  "app_and_generator_cpu_percent_one_core_100":self._process.cpu_percent(),
                  "host_cpu_percent":psutil.cpu_percent(),"pool_backend_rss_mb":0,
                  "pool_backend_cpu_percent_one_core_100":0}
        for pid in backend_pids:
            try:
                if pid not in self._backends:
                    self._backends[pid] = psutil.Process(pid)
                    self._backends[pid].cpu_percent()
                child = self._backends[pid]
                sample["pool_backend_rss_mb"] += child.memory_info().rss/1048576
                sample["pool_backend_cpu_percent_one_core_100"] += child.cpu_percent()
            except psutil.Error:
                pass
        sample["resource_sampling_duration_ms"] = round((time.perf_counter()-started)*1000,3)
        return sample

    async def sample(self, backend_pids):
        started = time.perf_counter()
        sample = await self._call(self._sample, tuple(backend_pids))
        sample["resource_sampling_await_ms"] = round((time.perf_counter()-started)*1000,3)
        return sample


def completed_arrival(task, active, stats):
    """Always retrieve exceptions, even when a task finishes before final gather."""
    active.discard(task)
    error = "cancelled" if task.cancelled() else task.exception()
    if error is not None:
        stats.status[stats.phase+"/arrival_task"]["task_error"] += 1
        if len(stats.unexpected)<100:
            stats.unexpected.append({"kind":"arrival_task","error":type(error).__name__,"detail":str(error)[:200]})


def pooled_connection_factory(pool, stats):
    class MeasuredConnection(LanSharePostgresConnection):
        def execute(self, sql, params=None):
            start = time.perf_counter()
            try:
                return super().execute(sql, params)
            finally:
                stats.timing(stats.sql_time, stats.phase + "/" + LABEL.get(), (time.perf_counter()-start)*1000)

    def connect():
        start = time.perf_counter()
        try:
            raw = pool.getconn(timeout=10)
        finally:
            stats.timing(stats.pool_wait, stats.phase + "/" + LABEL.get(), (time.perf_counter()-start)*1000)
        with stats.lock:
            stats.backend_pids.add(raw.info.backend_pid)
        return MeasuredConnection(raw, pool=pool)
    return connect


async def exercise(fixture, connect, pool, stats, args, reminder):
    app = fixture["app"]
    app.include_router(profile_routes.router)
    app.include_router(teaching_routes.router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    revisions = {}
    resume_ids = {}
    resume_layouts = {}
    resume_revisions = {}
    versions = {}
    randomizer = random.Random(args.seed)
    read_students = list(range(1,args.users+1))
    random.Random(args.seed).shuffle(read_students)
    limiter = asyncio.Semaphore(20)
    with connect() as conn:
        conn.execute("ALTER TABLE students ADD COLUMN avatar_file_hash TEXT DEFAULT ''")
        conn.execute("ALTER TABLE students ADD COLUMN avatar_mime_type TEXT DEFAULT ''")
        majors = ["英语", "软件工程", "网络工程", "护理学", "会计学", "汉语言文学", "环境设计", "旅游管理", "机械设计制造及其自动化", "国际经济与贸易"]
        for index, major in enumerate(majors, 1):
            conn.execute("INSERT INTO classes(id,name,major,program_duration_years) VALUES(?,?,?,4) ON CONFLICT(id) DO UPDATE SET major=EXCLUDED.major", (index, f"合成班{index}", major))
        conn.execute("UPDATE students SET class_id=(id-1)%10+1 WHERE id<=?", (args.users,))
        conn.executemany("INSERT INTO resume_personal_info(student_id,name,email,expected_position,revision) VALUES(?,?,?,?,1) ON CONFLICT(student_id) DO NOTHING",
                         [(sid, f"合成学生{sid}", f"student{sid}@example.invalid", "合成目标岗位") for sid in range(1,args.users+1)])
        conn.executemany("INSERT INTO resume_educations(student_id,kind,school,major,degree,start_date,end_date) VALUES(?,'university','合成大学',?,'本科','2024-09','2028-06')",
                         [(sid,majors[(sid-1)%10]) for sid in range(1,args.users+1)])
        conn.executemany("INSERT INTO resume_experiences(student_id,kind,title,start_date,end_date,role,content,contribution,achievement) VALUES(?,? ,?,'2025-03','2025-06','小组成员',?,?,?)",
                         [(sid,"course_project" if sid%2 else "internship",f"{majors[(sid-1)%10]}合成实践项目",
                           f"整理专业资料、进行需求访谈、完成小组讨论。学生{sid}承担资料分类与成果说明。"*3,
                           "整理调研记录并协助团队完成方案。",f"完成{sid%12+1}份合成练习记录，供测试资料核对。") for sid in range(1,args.users+1)])
        conn.executemany("INSERT INTO resume_skills(student_id,name,level,description) VALUES(?,'资料整理与沟通','熟悉',?)",
                         [(sid,f"课程实践中整理结构化资料并参与小组沟通，合成案例{sid}。") for sid in range(1,args.users+1)])
        conn.executemany("INSERT INTO resume_self_intros(student_id,title,content_md) VALUES(?,'合成自我介绍',?)",
                         [(sid,f"我是{majors[(sid-1)%10]}学生，参与专业课程实践和小组讨论，关注{('公共服务','专业技术','企业运营')[sid%3]}方向。所有材料为负载测试合成内容。") for sid in range(1,args.users+1)])
        materials = {section:{int(row["student_id"]):int(row["id"]) for row in conn.execute(f"SELECT student_id,MAX(id) AS id FROM {table} GROUP BY student_id")}
                     for section,table in (("education","resume_educations"),("experience","resume_experiences"),("skill","resume_skills"),("self_intro","resume_self_intros"))}
        for sid in range(1, args.users+1):
            resume_layouts[sid] = {"blocks":[{"type":section,"ids":[ids[sid]]} for section,ids in materials.items()]}
            rid = documents.create_resume(conn, sid, title=f"学生{sid}的合成草稿", template_key="classic", target_position="合成目标岗位", layout=resume_layouts[sid], draft=True)
            resume_ids[sid] = rid
            resume_revisions[sid] = 1
        conn.commit()
        teaching_dataset = seed_teaching(conn)

    async with httpx.AsyncClient(transport=transport, base_url="http://isolated-probe", timeout=30) as client:
        async def request(kind, sid, method, path, body=None, expected=200, measured=True):
            token = LABEL.set(kind)
            key = stats.phase + "/" + kind
            start = time.perf_counter()
            status = 0
            response = None
            try:
                response = await client.request(method, path, json=body, headers={"x-probe-student": str(sid)})
                status = response.status_code
                if measured:
                    stats.status[key][str(status)] += 1
                    stats.bytes[key] += len(response.content)
                    if status == expected == 409:
                        stats.expected_conflicts += 1
                    elif status != expected and len(stats.unexpected) < 100:
                        stats.unexpected.append({"kind": kind, "status": status, "expected": expected, "detail": response.text[:300]})
                return response
            except Exception as exc:
                if measured:
                    stats.status[key]["transport_error"] += 1
                    if len(stats.unexpected) < 100:
                        stats.unexpected.append({"kind": kind, "error": type(exc).__name__, "detail": str(exc)[:200]})
                raise
            finally:
                if measured:
                    stats.timing(stats.requests, key, (time.perf_counter()-start)*1000)
                LABEL.reset(token)

        async def initialize(sid):
            async with limiter:
                response = await request("initialize", sid, "POST", "/api/career-path/initialize", measured=False)
                if response.status_code != 200:
                    raise RuntimeError(f"Synthetic initialize failed: {response.status_code} {response.text[:300]}")
                state = response.json()
                versions[sid] = state["result_version"]
                revisions[sid] = state["revision"]
        await asyncio.gather(*(initialize(sid) for sid in range(1,args.users+1)))
        print(f"Seeded {args.users} students / 10 majors / {args.users} drafts; pooled connections={pool.get_stats().get('pool_size')}", flush=True)

        async def read(index, baseline=None, measured=True):
            sid = read_students[index % args.users]
            fraction = index % 10
            if baseline == "avatar" or (baseline is None and fraction == 9):
                kind, path = "existing_profile_avatar", f"/api/profile/avatar?role=student&user_id={sid}"
            elif baseline == "teaching" or fraction == 5:
                kind, path = "teaching_course_schedule", "/api/dashboard/course-schedule/overview"
            elif fraction < 5:
                kind, path = "career_light_state", "/api/career-path/state?known_result_version=" + versions[sid]
            elif fraction < 8:
                kind, path = "resume_compact", "/api/resume/resumes?compact=true"
            else:
                kind, path = "resume_readiness", "/api/resume/readiness"
            response = await request(kind, sid, "GET", path, measured=measured)
            if not measured and response.status_code != 200:
                raise AssertionError(f"{kind} warmup failed: {response.status_code} {response.text[:200]}")
            if kind == "teaching_course_schedule" and response.status_code == 200:
                overview = response.json()["overview"]
                lessons = [lesson for week in overview["weeks"] for lesson in week["lessons"]]
                expected_class = (sid-1)%10+1
                expected_offerings = set(range((expected_class-1)*3+1,expected_class*3+1))
                assert len(lessons) == 120 and {lesson["class_offering_id"] for lesson in lessons} == expected_offerings, "Teaching fixture returned missing or foreign lessons"
            if kind == "career_light_state" and response.status_code == 200:
                data = response.json()
                versions[sid] = data["result_version"]
                stats.light["unchanged" if data.get("network_unchanged") else "changed_full"] += 1

        async def arrival_stream(duration, rps, action):
            started = time.perf_counter()
            active = set()
            for index in range(math.ceil(duration*rps)):
                due = started + index/rps
                await asyncio.sleep(max(0, due-time.perf_counter()))
                if time.perf_counter() >= started+duration:
                    stats.skipped += math.ceil(duration*rps)-index
                    break
                stats.arrival_dispatch_lag.append(max(0,time.perf_counter()-due)*1000)
                if len(active) >= args.max_inflight:
                    stats.skipped += 1
                    continue
                task = asyncio.create_task(action(index))
                active.add(task)
                task.add_done_callback(lambda completed:completed_arrival(completed,active,stats))
            if active:
                await asyncio.gather(*active,return_exceptions=True)

        # Warm up each real endpoint explicitly; keep it outside all baselines.
        for kind in ("avatar","teaching"):
            stats.phase = "warmup_"+kind
            for index in range(10):
                await read(index,kind,measured=False)
            stats.phase = "baseline_"+kind
            await arrival_stream(args.baseline_duration,args.rps,lambda index,kind=kind:read(index,kind))
        print(f"Separate avatar/teaching baselines complete: each {args.baseline_duration}s at {args.rps} RPS", flush=True)

        async def writer(sid):
            await asyncio.sleep((sid-1)*args.save_interval/args.writers)
            sequence = 0
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                conflict = randomizer.random() < args.conflict_rate
                if (sid + sequence) % 2:
                    revision = revisions[sid]
                    response = await request("career_progress", sid, "POST", "/api/career-path/progress", {
                        "answers": [], "mode": "quick", "quiz_version": "career-quiz-v2", "revision": revision + int(conflict)}, expected=409 if conflict else 200)
                    if response.status_code == 200:
                        revisions[sid] = response.json()["revision"]
                else:
                    revision = resume_revisions[sid]
                    response = await request("resume_save", sid, "PUT", f"/api/resume/resumes/{resume_ids[sid]}", {
                        "revision": revision + int(conflict), "draft": True, "title": f"学生{sid}第{sequence}次保存", "template_key": "classic",
                        "target_position": "合成目标岗位", "layout": resume_layouts[sid]}, expected=409 if conflict else 200)
                    if response.status_code == 200:
                        resume_revisions[sid] = response.json()["revision"]
                sequence += 1
                await asyncio.sleep(max(0, min(deadline-time.perf_counter(),args.save_interval-(time.perf_counter()-started))))

        async def enqueue(index):
            await request("suggestion_enqueue", index % args.users + 1, "POST", "/api/resume/self-intro/optimize",
                          {"text": f"合成学生介绍，参与课程讨论和资料整理。负载种子{args.seed}，任务{index}。"}, expected=202)

        async def monitor():
            previous_started = None
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                sample = {"elapsed_s":round(started-mixed_start,2),"pool":pool.get_stats(),"stub_active":stats.current_stub,
                          "monitor_period_ms":round((started-previous_started)*1000,3) if previous_started is not None else None}
                previous_started = started
                with stats.lock:
                    backend_pids = tuple(stats.backend_pids)
                sample.update(await resource_sampler.sample(backend_pids))
                def database_waits():
                    token = LABEL.set("monitor")
                    try:
                        with connect() as conn:
                            rows = conn.execute("SELECT state,wait_event_type,wait_event,COUNT(*) AS count FROM pg_stat_activity WHERE application_name=? GROUP BY state,wait_event_type,wait_event", ("career-mixed-"+fixture["schema"],)).fetchall()
                            return [dict(row) for row in rows]
                    finally:
                        LABEL.reset(token)
                sample["database_activity"] = await asyncio.to_thread(database_waits)
                sample["monitor_work_duration_ms"] = round((time.perf_counter()-started)*1000,3)
                stats.resources.append(sample)
                if len(stats.resources) % 30 == 0:
                    progress = {"elapsed_s":sample["elapsed_s"],"mixed_requests":sum(len(v) for k,v in stats.requests.items() if k.startswith("mixed/")),
                                "unexpected_count":len(stats.unexpected),"expected_conflicts":stats.expected_conflicts,"pool":sample["pool"],"last_resource":sample}
                    Path(args.output).with_suffix(".progress.json").write_text(json.dumps(progress,ensure_ascii=False,indent=2),encoding="utf-8")
                    print(f"Mixed progress {sample['elapsed_s']:.0f}s: {progress['mixed_requests']} requests, {len(stats.unexpected)} unexpected, RSS {sample['app_and_generator_rss_mb']} MB",flush=True)
                await asyncio.sleep(1)

        stats.phase = "mixed"
        async with ResourceSampler() as resource_sampler:
            await asyncio.to_thread(reminder.prepare,args.duration,args.reminder_interval,args.reminder_offset)
            mixed_start = time.perf_counter()
            cpu_start = await resource_sampler.cpu_seconds()
            deadline = mixed_start + args.duration
            worker_label = LABEL.set("background_jobs")
            try:
                worker.start_student_career_job_workers()
                reminder.start()
            finally:
                LABEL.reset(worker_label)
            monitor_task = asyncio.create_task(monitor())
            try:
                await asyncio.gather(arrival_stream(args.duration,args.rps,read), arrival_stream(args.duration,args.job_rps,enqueue),
                                     *(writer(sid) for sid in range(1,args.writers+1)), monitor_task)
            finally:
                if not monitor_task.done():
                    monitor_task.cancel()
                await asyncio.gather(monitor_task,return_exceptions=True)
                await worker.stop_student_career_job_workers()
                reminder_report = await reminder.finish()
            elapsed = time.perf_counter()-mixed_start
            cpu_seconds = await resource_sampler.cpu_seconds()-cpu_start
        with connect() as conn:
            jobs = [dict(row) for row in conn.execute("SELECT task_type,status,COUNT(*) AS count FROM ai_jobs GROUP BY task_type,status")]
            majors_count = conn.execute("SELECT COUNT(*) FROM career_major_networks").fetchone()[0]
        for kind in ("avatar","teaching"):
            stats.phase = "post_baseline_"+kind
            await arrival_stream(args.baseline_duration,args.rps,lambda index,kind=kind:read(index,kind))
        stats.phase = "verification"
        reminder_report = await asyncio.to_thread(reminder.verify_duplicate_replay)
        reminder_report["latency"] = {event:distribution([r[event] for r in reminder_report["records"] if event in r])
            for event in ("claim_delay_ms","handler_start_delay_ms","handler_duration_ms","notification_visible_delay_ms","done_delay_ms")}
        return {"elapsed_seconds_including_drain": round(elapsed, 3), "mixed_app_and_generator_cpu_seconds":round(cpu_seconds,3),
                "machine":resource_sampler.machine,
                "mixed_app_and_generator_cpu_average_percent_one_core_100":round(cpu_seconds/elapsed*100,2), "jobs": jobs, "major_networks": majors_count,
                "worker": worker.student_career_worker_snapshot(), "pool": pool.get_stats(),"teaching_dataset":teaching_dataset,
                "assignment_reminders":reminder_report,"warmup":{"career_initialize_students":args.users,"avatar_requests":10,"teaching_requests":10,"included_in_latency_samples":False}}


def run(args):
    stats = Measurements()
    started = time.time()
    hashes_before = source_hashes()
    with isolated_career_postgres(students=args.users) as fixture, ExitStack() as stack:
        schema = fixture["schema"]
        pool = ConnectionPool(config.DATABASE_URL, min_size=2, max_size=12, timeout=10,
            kwargs={"row_factory": sqlite_compatible_dict_row, "options": f"-c search_path={schema} -c application_name=career-mixed-{schema} -c statement_timeout=30000 -c lock_timeout=5000"}, open=True)
        pool.wait(timeout=15)
        stack.callback(pool.close)
        connect = pooled_connection_factory(pool, stats)
        # Every already-imported domain reference uses the same bounded pool.
        for name, module in list(sys.modules.items()):
            if name.startswith("classroom_app") and hasattr(module,"get_db_connection"):
                stack.enter_context(patch.object(module,"get_db_connection",connect))
        # The scheduler schema/service import their own engine references.
        for name in ("classroom_app.db.schema_scheduler","classroom_app.services.scheduled_task_service","classroom_app.services.message_center_service"):
            stack.enter_context(patch.object(sys.modules[name],"get_configured_db_engine",return_value="postgres"))
        reminder = ReminderMeasurements(connect,LABEL,poll_seconds=args.scheduler_poll_seconds)
        reminder.install(stack)
        stack.enter_context(patch.object(worker,"AI_CONCURRENCY",2))
        stack.enter_context(patch.object(worker,"CAREER_JOBS_ENABLED",True))
        stack.enter_context(patch.object(worker,"POLL_SECONDS",1))
        async def stub(job, payload):
            stats.current_stub += 1
            stats.max_stub = max(stats.max_stub,stats.current_stub)
            stats.stub_count += 1
            try:
                await asyncio.sleep(args.stub_seconds)
                if job["task_type"] == "career_major_network_generate":
                    return {"network": baseline_network(payload["major_name"]), "sources": {"synthetic_only": True}}
                if job["task_type"] == "resume_suggestion":
                    return {"ok": True, "content": "合成介绍：参与课程讨论与资料整理。"}
                raise RuntimeError("Unexpected model job in isolated performance probe")
            finally:
                stats.current_stub -= 1
        handlers = dict(registry.registered_student_career_handlers())
        for kind in ("career_major_network_generate","resume_suggestion"):
            original = handlers[kind]
            registry.register_student_career_handler(kind,execute=stub,apply=original.apply,fail=original.fail,timeout_seconds=30,lane=original.lane)
        try:
            details = asyncio.run(exercise(fixture,connect,pool,stats,args,reminder))
        finally:
            for kind in ("career_major_network_generate","resume_suggestion"):
                original = handlers[kind]
                registry.register_student_career_handler(kind,execute=original.execute,apply=original.apply,fail=original.fail,timeout_seconds=original.timeout_seconds,lane=original.lane)
    groups = {}
    for key, timings in stats.requests.items():
        groups[key] = {**stats.summarize(stats.requests,key),"statuses":dict(stats.status[key]),"response_bytes":stats.bytes[key],
                       "database_execute_calls":stats.timing_totals[(id(stats.sql_time),key)]}
    resources = stats.resources
    hashes_after = source_hashes()
    changed_sources = sorted(name for name in set(hashes_before)|set(hashes_after) if hashes_before.get(name)!=hashes_after.get(name))
    mixed_requests = sum(len(values) for key,values in stats.requests.items() if key.startswith("mixed/"))
    errors_5xx = sum(count for statuses in stats.status.values() for status,count in statuses.items() if status.isdigit() and int(status)>=500)
    return {"ok":not stats.unexpected and stats.skipped == 0 and stats.max_stub <= 2 and details["assignment_reminders"]["ok"],
        "synthetic_only":True,"isolated_schema_removed":True,"schema":schema,"configuration":vars(args),
        "dataset":{"majors":10,"students":args.users,"resumes_per_student":1,"materials_per_student":{"education":1,"experience":1,"skill":1,"self_intro":1},"note":"Fixture students 1/2 retain seed profile; student1 additionally retains fixture internship."},
        "clock":{"process_TZ":os.environ.get("TZ"),"application_timezone":os.environ.get("APP_TIMEZONE"),"naive_scheduler_matches_china_clock":True},
        "measurement_scope":"Real ASGI routes/services, threadpool and PostgreSQL pool max12. No network/TLS/browser. App CPU/RSS includes the load generator and dedicated reminder thread. Pooled DB backend RSS can double-count shared pages; excludes shared PG daemon/checkpointer. Separate real profile-avatar and teaching course-schedule baselines. Read mix stays 50 RPS by replacing 10% career state with teaching (state50/compact20/readiness10/teaching10/avatar10 percent). Real assignment reminder scheduler shares this pool; SMTP enqueue/delivery disabled. Desktop differs from production 2 CPU / 3.57 GB; this is not a production SLA.",
        "wall_seconds_with_setup":round(time.time()-started,3),"requests":groups,
        "source_hashes_before":hashes_before,"sources_changed_during_run":changed_sources,"fixed_code_during_run":not changed_sources,
        "mixed_requests_total":mixed_requests,"mixed_achieved_rps":round(mixed_requests/details["elapsed_seconds_including_drain"],3),"http_5xx_count":errors_5xx,
        "database_checkout_wait":{key:stats.summarize(stats.pool_wait,key) for key in stats.pool_wait if not key.startswith("setup/")},
        "database_execute_time_including_server_wait":{key:stats.summarize(stats.sql_time,key) for key in stats.sql_time if not key.startswith("setup/")},
        "expected_409_conflicts":stats.expected_conflicts,"unexpected_responses":stats.unexpected,"arrival_skipped":stats.skipped,
        "arrival_dispatch_lag":distribution(stats.arrival_dispatch_lag),"lightweight_responses":dict(stats.light),
        "stub":{"max_concurrent":stats.max_stub,"executions":stats.stub_count,"duration_seconds":args.stub_seconds},
        "resource_summary":{"app_rss_max_mb":max((r["app_and_generator_rss_mb"] for r in resources),default=0),
                            "app_cpu_max_percent_one_core_100":max((r["app_and_generator_cpu_percent_one_core_100"] for r in resources if r["elapsed_s"]>=1),default=0),
                            "sampling_duration":distribution([r["resource_sampling_duration_ms"] for r in resources]),
                            "sampling_await":distribution([r["resource_sampling_await_ms"] for r in resources]),
                            "monitor_work_duration":distribution([r["monitor_work_duration_ms"] for r in resources]),
                            "monitor_period":distribution([r["monitor_period_ms"] for r in resources if r["monitor_period_ms"] is not None])},
        "resource_samples":resources,**details}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration",type=float,default=60)
    parser.add_argument("--rps",type=float,default=50)
    parser.add_argument("--users",type=int,default=1000)
    parser.add_argument("--writers",type=int,default=100)
    parser.add_argument("--save-interval",type=float,default=10)
    parser.add_argument("--baseline-duration",type=float,default=10)
    parser.add_argument("--job-rps",type=float,default=1)
    parser.add_argument("--stub-seconds",type=float,default=2)
    parser.add_argument("--conflict-rate",type=float,default=.05)
    parser.add_argument("--max-inflight",type=int,default=200)
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--reminder-interval",type=float,default=30)
    parser.add_argument("--reminder-offset",type=float,default=10)
    parser.add_argument("--scheduler-poll-seconds",type=float,default=20)
    parser.add_argument("--output",default=".codex-temp/career-mixed-load.json")
    args = parser.parse_args()
    if not (1<=args.duration<=3600 and 0<args.rps<=200 and 10<=args.users<=1000 and 1<=args.writers<=args.users and args.save_interval>=1 and 0<args.job_rps<=5 and 0<=args.conflict_rate<=1 and args.reminder_interval>=5 and args.reminder_offset>=1 and args.scheduler_poll_seconds>=5):
        parser.error("duration 1..3600, rps (0,200], users 10..1000, writers 1..users, save interval >=1, job RPS (0,5], conflict rate 0..1 required")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    result = run(args)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"ok":result["ok"],"report":str(output),"requests":result["requests"],"expected_409_conflicts":result["expected_409_conflicts"],"unexpected_responses":result["unexpected_responses"],"stub":result["stub"],"resource_summary":result["resource_summary"]},ensure_ascii=False,indent=2))
    sys.exit(0 if result["ok"] else 1)
