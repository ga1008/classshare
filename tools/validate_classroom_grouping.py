"""Exercise the built classroom UI with a new 63-student synthetic offering."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "tests/e2e/scripts"


def prepare(runtime: Path, *, project_topics=False) -> None:
    subprocess.run([sys.executable, str(SCRIPTS / "prepare_ui_v3_runtime.py"),
                    "--runtime-root", str(runtime)], cwd=ROOT, check=True)
    path = runtime / "fixture.json"
    fixture = json.loads(path.read_text("utf-8"))
    database = runtime / "db/classroom.db"
    if fixture.get("uiV3Synthetic") is not True or Path(fixture["databasePath"]).resolve() != database:
        raise ValueError("Grouping fixture must use the newly created synthetic database")
    with closing(sqlite3.connect(database)) as conn, conn:
        teacher, offering, primary = fixture["teacher"]["id"], fixture["classOfferingId"], fixture["classId"]
        secondary = conn.execute("INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)",
                                 ("Python QA B", teacher)).lastrowid
        for class_id in (primary, secondary):
            conn.execute("""INSERT INTO class_offering_class_links (offering_id, class_id, teacher_id, is_primary)
                            VALUES (?, ?, ?, ?) ON CONFLICT (offering_id, class_id) DO NOTHING""",
                         (offering, class_id, teacher, int(class_id == primary)))
        for i in range(2, 65):
            student = conn.execute("INSERT INTO students (student_id_number, name, class_id) VALUES (?, ?, ?)",
                                   (f"GROUP-QA-{i:02d}", f"合成学生{i:02d}", primary if i % 2 else secondary)).lastrowid
        conn.execute("""INSERT INTO classroom_retake_students (class_offering_id, student_id, status)
                        VALUES (?, ?, 'confirmed')""", (offering, student))
        assignment = conn.execute(
            """INSERT INTO assignments (course_id, class_offering_id, title, status, grading_mode, availability_mode)
               VALUES (?, ?, '小组作业流程验收', 'published', 'manual', 'permanent')""",
            (fixture["courseId"], offering),
        ).lastrowid
        fixture["groupingQa"] = {"exemptId": student, "assignmentId": assignment}
        if project_topics:
            conn.execute("UPDATE courses SET name=? WHERE id=?", ("Python 程序设计", fixture["courseId"]))
            conn.executemany("UPDATE classes SET name=? WHERE id=?",
                             [("项目实践合成班 A", primary), ("项目实践合成班 B", secondary)])
            conn.execute("UPDATE class_offering_sessions SET title='第' || order_index || '次课 · Python 程序实践' "
                         "WHERE class_offering_id=?", (offering,))
            topics_class = conn.execute("INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)",
                                        ("项目研习合成班", teacher)).lastrowid
            course = conn.execute("INSERT INTO courses (name,created_by_teacher_id) VALUES (?,?)",
                                  ("环境数据研习", teacher)).lastrowid
            second_offering = conn.execute(
                "INSERT INTO class_offerings (course_id,class_id,teacher_id) VALUES (?,?,?)",
                (course, topics_class, teacher),
            ).lastrowid
            conn.executemany("INSERT INTO students (student_id_number,name,class_id) VALUES (?,?,?)",
                             [(f"FIELD-QA-{i:03d}", f"研习学生{i:02d}", topics_class) for i in range(24)])
            fixture["projectTopicsQa"] = {"otherCourse": second_offering}
            prepare_packages(runtime)
    path.write_text(json.dumps(fixture), encoding="utf-8")


def prepare_packages(runtime: Path) -> None:
    import fitz

    campus_titles = [
        "校园图书借阅趋势分析", "自习室预约与余位查询", "食堂消费与营养记录",
        "失物招领信息匹配", "校园通勤路线规划", "班级账本与收支核对",
        "运动打卡数据可视化", "社团活动报名与分场安排", "二手教材交换目录",
        "教学楼能耗周报", "实验室设备借还管理", "宿舍报修进度查询",
        "课程作业提醒与日历", "校园气象观测数据清洗与异常值复核",
    ]
    for count, file_type in [(14, "html"), (8, "pdf")]:
        topics = []
        with zipfile.ZipFile(runtime / f"{file_type}-projects.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("公共说明.txt", "各小组完成自己的项目后向老师演示。")
            for index in range(count):
                key = f"field-{chr(97 + index)}"
                entry = f"{key}/项目要求.{file_type}"
                title = campus_titles[index] if file_type == "html" else f"观测课题{index + 1:02d}"
                topics.append({"key": key, "number": str(index + 1).zfill(2), "title": title, "entry": entry,
                               "brief": "用 Python 读取样例数据，完成查询与统计，并用图表展示结果。"
                                        if file_type == "html" else f"采集第{index + 1}号样区的观测数据。",
                               "pool": "reserve" if index == 13 else "regular"})
                if file_type == "html":
                    attachment = f"{key}/样例数据.csv"
                    topics[-1]["attachments"] = [attachment]
                    bundle.writestr(attachment, f"record,value\n{key},12\n")
                    bundle.writestr(entry, f'<!doctype html><meta charset="utf-8"><link rel="stylesheet" '
                                    f'href="style.css"><h1>{title}</h1><p>本组的观测任务。</p>')
                    bundle.writestr(f"{key}/style.css", "h1 { color: rgb(19, 97, 53); }")
                else:
                    with fitz.open() as doc:
                        page = doc.new_page()
                        page.insert_text((60, 60), f"Field project {key}")
                        bundle.writestr(entry, doc.tobytes())
            manifest = {"formatVersion": 1, "packageKey": f"field-{file_type}", "version": "qa-1",
                        "title": "Python 校园数据项目" if file_type == "html" else "环境数据研习",
                        "common": ["公共说明.txt"], "topics": topics}
            bundle.writestr("project-topics.json", json.dumps(manifest, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("grouping", "topics"), default="grouping")
    suite = parser.parse_args().suite
    scratch = ROOT / ".codex-temp/scratch"
    evidence = ROOT / ".codex-temp/evidence"
    scratch.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    prefix = "classroom-project-topics-" if suite == "topics" else "classroom-grouping-"
    output = Path(tempfile.mkdtemp(prefix=prefix, dir=evidence))
    print(f"Classroom browser evidence: {output}", flush=True)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=scratch) as work:
        runtime = Path(work) / "runtime"
        prepare(runtime, project_topics=suite == "topics")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = dict(os.environ, P03_RUNTIME_ROOT=str(runtime), UI_V3_PORT=str(port), UI_V3_OUTPUT=str(output),
                   TMP=work, TEMP=work, TMPDIR=work, PWTEST_CACHE_DIR=str(Path(work) / "playwright-cache"))
        with (output / "server.log").open("wb") as log:
            server = subprocess.Popen([sys.executable, str(SCRIPTS / "serve_ui_v3.py"), "--runtime-root",
                                       str(runtime), "--port", str(port)], cwd=ROOT, env=env,
                                      stdout=log, stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and server.poll() is None:
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/api/internal/health", timeout=2):
                            break
                    except (OSError, URLError):
                        time.sleep(0.3)
                else:
                    raise RuntimeError(f"Synthetic server failed to start. See {output / 'server.log'}")
                subprocess.run(["node", str(ROOT / "node_modules/@playwright/test/cli.js"), "test", "-c",
                                "tests/e2e/ui-v3.playwright.config.ts",
                                "classroom-project-topics.synthetic.ts" if suite == "topics" else "classroom-grouping.synthetic.ts",
                                "--workers=1"], env=env, cwd=ROOT, check=True)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
