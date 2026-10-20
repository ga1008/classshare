"""Real routes, storage and JWXT adapter with an isolated synthetic HTTP transport."""
import argparse
import asyncio
import json
import runpy
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--runtime-root", required=True, type=Path)
args, _ = parser.parse_known_args()
runtime = args.runtime_root.resolve()
assert runtime.is_relative_to(ROOT / ".codex-temp") and runtime != ROOT / ".codex-temp"
fixture = json.loads((runtime / "fixture.json").read_text("utf-8"))
assert fixture["teacherTimetable"]["synthetic"] and Path(fixture["databasePath"]).resolve() == runtime / "db/classroom.db"
from tools.isolated_environment import isolate_sqlite_environment, guard_dotenv_loading, guard_postgres_connections
isolate_sqlite_environment(runtime)
guard_dotenv_loading()
guard_postgres_connections()
import httpx
from classroom_app.services import academic_teacher_timetable_service as service
from classroom_app.services import academic_teacher_timetable_adapter as adapter

INDEX_HTML = """<select id="xnm"><option value="2026">2026-2027</option></select>
<select id="xqm"><option value="3">1</option><option value="12">2</option></select>
<select id="xqh_id"><option value="1">合成校区</option></select>
<select id="jg_id"><option value="04">合成数字学院</option></select>
<select id="kkbm_id"><option value="04">合成数字学院</option></select>
<select id="zcm"><option value="013">讲师</option></select>
<select id="sfwp"><option value="0">否</option><option value="1">是</option></select>"""


def control():
    return json.loads((runtime / "teacher-timetable-control.json").read_text("utf-8"))


async def respond(request):
    state = control()
    form = {key: values[-1] for key, values in parse_qs(request.content.decode(), keep_blank_values=True).items()}
    with (runtime / "teacher-timetable-requests.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"path": request.url.path, "method": request.method, "form": form, "mode": state["mode"]}, ensure_ascii=False) + "\n")
    if request.method == "GET":
        return httpx.Response(200, text=INDEX_HTML)
    if request.url.path.endswith("cxJskbdyList.html"):
        if state.get("delay_ms"):
            await asyncio.sleep(state["delay_ms"] / 1000)
        if state["mode"] == "error":
            return httpx.Response(503, text="Synthetic maintenance")
        if state["mode"] == "malformed":
            return httpx.Response(200, json={"items": []})
        count = 14 if form["jgh"] == "分页" or form["jgh"].startswith("000") else 2
        people = [{"jgh": f"000{123 + index}", "xm": "同名教师", "jgh_id": f"opaque-{index}",
                   "jg_id": "04", "jgmc": "合成数字学院" if index == 0 else "合成语言学院",
                   "xnm": form["xnm"], "xqm": form["xqm"], "xqh_id": "1",
                   "zcmc": "副教授" if state.get("revision") == 2 else "讲师", "zcm": "013"}
                  for index in range(count)]
        if form["jgh"] == "无匹配":
            people = []
        elif form["jgh"].startswith("000"):
            people = [person for person in people if person["jgh"] == form["jgh"]]
        page = int(form["queryModel.currentPage"])
        # Two rows per upstream page exercises real complete pagination.
        return httpx.Response(200, json={"items": people[(page - 1) * 2:page * 2], "totalCount": len(people),
                                        "totalPage": (len(people) + 1) // 2, "currentPage": page})
    if request.url.path.endswith("cxXnxqsfkz.html"):
        return httpx.Response(200, json="false")
    no = form["jgh"]
    lessons = [] if state["mode"] == "empty_schedule" else [{
        "jgh": no, "xnm": form["xnm"], "xqm": form["xqm"], "kch": "CS101",
        "kcmc": "合成数据库原理" if no == "000123" else "合成英语阅读", "jxb_id": "CLASS-" + no,
        "jxbmc": "课程-" + no, "jxbzc": "合成软件2601班", "xqj": "3", "jc": "2-3节",
        "jcs": "2-3", "zcd": "1-20周", "cdmc": "更新楼C201" if state.get("revision") == 2 else "合成楼B412", "xkrs": "35",
        "kcxszc": "理论:16,实践:16", "zxs": "32", "xf": "2", "xqmc": "合成校区",
    }]
    if lessons and state.get("revision") == 2:
        lessons.append({**lessons[0], "kch": "CS202", "kcmc": "合成新增课程", "jxb_id": "NEW-" + no,
                        "xqj": "5", "jc": "4-5节", "jcs": "4-5"})
    unscheduled = "更新安全教育" if state.get("revision") == 2 else "合成安全教育"
    return httpx.Response(200, json={"kbList": lessons,
                                   "sjkList": [{"kcmc": unscheduled, "qtkcgs": f"{unscheduled}/1-12周/安全教育-01/合成班"}],
                                   "bjsjkList": [], "jxhjkcList": []})


@asynccontextmanager
async def authenticated(_credential):
    if control()["mode"] == "login_error":
        raise ValueError("合成账号登录过期")
    async with httpx.AsyncClient(base_url="https://academic.synthetic.invalid", transport=httpx.MockTransport(respond)) as client:
        yield client, SimpleNamespace(school_code="gxufl"), {}


def credential(_conn, teacher_id, **_kwargs):
    if teacher_id != fixture["teacher"]["id"]:
        return None
    return {"credential_id": fixture["teacherTimetable"]["credentialId"], "school_code": "gxufl", "username": "synthetic-query-user"}


service.open_authenticated_academic_client = authenticated
service.load_teacher_academic_access_method = credential
adapter.REQUEST_INTERVAL = 0
runpy.run_path(str(ROOT / "tests/e2e/scripts/serve_ui_v3.py"), run_name="__main__")
