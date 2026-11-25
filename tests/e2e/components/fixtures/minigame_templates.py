"""Render the real minigame pages without initialising an app or opening a business DB."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from classroom_app.lq import lq_props
from classroom_app.lq_dialogs import lq_dialog_props
from classroom_app.services.manage_nav_service import build_manage_nav

built_assets = {}
if os.getenv("MINIGAMES_TEST_BUILT_ASSETS") == "1":
    built_assets = json.loads((ROOT / "static/assets/manifest.json").read_text(encoding="utf-8"))["entries"]


def asset_url(key):
    source = "css/tailwind-app.css" if key == "tailwind_app" else key
    return "/static/" + built_assets.get(source, source)


env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
env.globals.update(
    lq_props=lq_props, lq_dialog_props=lq_dialog_props,
    asset_url=asset_url,
    vite_entry_tags=lambda *a: "", static_asset_revision=lambda: "fixture",
    lq_family_enabled=lambda family: family == "manage-shell", site_record="",
    resolve_user_ui_preferences=lambda *a: {"enabled": False},
    ai_workspace_policy=lambda *a: {"allowed": False},
)
data = json.load(sys.stdin)
role = "teacher" if data["role"] == "super" else data["role"]
user = {"id": 97, "name": "合成教师" if role == "teacher" else "合成学生", "role": role}
page = data["page"]
guide_context = {}
if page == "author_guide":
    from classroom_app.services.minigame_author_kit import guide_page

    html, toc = guide_page()
    guide_context = {"guide_html": html, "guide_toc": toc}
active_page = "game_reviews" if page == "reviews" else "games"
is_super = data["role"] == "super" or (role == "teacher" and page == "reviews")
page_title = (
    "小游戏审核" if page == "reviews"
    else "小游戏开发规范" if page == "author_guide"
    else "我的作品" if role == "student"
    else "小游戏管理"
)
template_name = "minigames/" + page + ".html"
if page == "classroom":
    template_name = "classroom_main_v4.html"
    guide_context.update(
        classroom={"id": 123, "course_id": 55, "course_name": "网络工程", "class_name": "合成班"},
        classroom_page={"teaching_plan": {}, "hero": {}, "group_qr": {},
                        "assignment_workspace_items": [], "assignment_workspace_preview": {},
                        "activity_sidebar": {"initial_counts": {}}, "academic_course_exams": {},
                        "sections": {"assignment": {}, "discussion": {}, "resources": {}}},
    )
html = env.get_template(template_name).render(
    request=SimpleNamespace(query_params={}, url=SimpleNamespace(path="/manage/library/games/reviews" if page == "reviews" else "/games")),
    user_info=user, user=user,
    games=[data["game"]], game=data["game"], csrf_token="synthetic-only",
    sidebar=data.get("sidebar", {}), sidebar_hydrated=bool(data.get("sidebar")), preview_version_id=None,
    active_page=active_page, manage_nav=build_manage_nav(user, active_page, is_super_admin=is_super),
    page_title=page_title,
    current_teacher_is_super_admin=is_super, can_publish=is_super,
    genre_options=[{"id":"adventure","label":"冒险"},{"id":"puzzle","label":"解谜"}],
    discovery=data.get("preference", {}), active_genres=data.get("preference", {}).get("genres", []),
    active_tags=data.get("preference", {}).get("tags", []), tag_options=["像素风","3D"],
    scope_options={"class": [{"id": 3, "name": "合成行政班"}], "classroom": [{"id": 5, "name": "合成课堂"}]},
    **guide_context,
)
sys.stdout.buffer.write(html.encode("utf-8"))
