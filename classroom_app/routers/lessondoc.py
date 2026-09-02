"""LessonDoc 学习文档包 API(设计: docs/course-lessondoc-template-2026-09.md §7.3).

- POST /api/lessondoc/packs                         建包(向导提交)
- GET  /api/lessondoc/packs?course_id=              查课程的包列表(含逐课状态)
- GET  /api/lessondoc/packs/{pack_id}               包详情
- PUT  /api/lessondoc/packs/{pack_id}/theme         切默认主题(重渲首页)
- PUT  /api/lessondoc/packs/{pack_id}/lessons/{n}   更新单课(hint/排除/标题)
- POST /api/lessondoc/packs/{pack_id}/lessons/{n}/generate   生成/重写单课
- POST /api/lessondoc/packs/{pack_id}/generate-batch         顺序补齐
- POST /api/lessondoc/packs/{pack_id}/bind          绑定课堂(确定性,复用 HTML 包绑定)
- POST /api/lessondoc/packs/{pack_id}/refresh-assets 刷新包内引擎

鉴权:pack.teacher_id 本人;bind 另校验课堂 owner。
课次拆分建议不设新端点——向导复用既有 POST /courses/ai-generate-lessons。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..db.connection import begin_immediate_transaction
from ..dependencies import get_current_teacher
from ..services.lessondoc import assets as lessondoc_assets
from ..services.lessondoc import generate as lessondoc_generate
from ..services.lessondoc import pack_service, spec
from ..services.lessondoc.pack_service import LessonDocPackError
from ..services.lessondoc.validate import LessonDocValidationError

router = APIRouter()


# ---------------------------------------------------------------- 请求模型

class LessonPlanItem(BaseModel):
    n: int = Field(ge=1, le=200)
    title: str = ""
    topics: list[str] = Field(default_factory=list)
    lab: bool = False
    userHint: str = ""
    excluded: bool = False


class StageItem(BaseModel):
    label: str = ""
    lessons: list[int] = Field(default_factory=list)


class CreatePackRequest(BaseModel):
    course_id: int
    theme: str = spec.DEFAULT_THEME
    pack_name: str = ""
    intro: str = ""
    course_hint: str = Field(default="", max_length=3000)   # 课程级生成提示
    per_session_sections: int = 2
    lessons: list[LessonPlanItem]
    stages: list[StageItem] = Field(default_factory=list)
    generate_scope: str = "first2"   # first2 | all | none


class UpdateLessonRequest(BaseModel):
    user_hint: str | None = None
    excluded: bool | None = None
    title: str | None = None


class GenerateLessonRequest(BaseModel):
    mode: str = "generate"           # generate | rewrite
    user_hint: str = ""
    class_offering_id: int = 0
    session_id: int = 0


class BatchGenerateRequest(BaseModel):
    lesson_nos: list[int] = Field(default_factory=list)   # 空 = 全部 pending/failed
    limit: int = Field(default=0, ge=0, le=200)


class BindRequest(BaseModel):
    class_offering_ids: list[int]


class ThemeRequest(BaseModel):
    theme: str


class ImportLegacyRequest(BaseModel):
    root_material_id: int          # 旧手写 HTML 包的包根文件夹
    course_id: int
    theme: str = spec.DEFAULT_THEME
    pack_name: str = ""
    dry_run: bool = False          # 只试解析看告警，不落库


# ---------------------------------------------------------------- 辅助

def _ensure_course_access(conn, course_id: int, teacher_id: int) -> dict[str, Any]:
    course = conn.execute(
        "SELECT * FROM courses WHERE id = ? LIMIT 1", (int(course_id),)
    ).fetchone()
    if course is None:
        raise HTTPException(404, "课程不存在")
    if int(course["created_by_teacher_id"] or 0) == int(teacher_id):
        return dict(course)
    has_offering = conn.execute(
        "SELECT 1 FROM class_offerings WHERE course_id = ? AND teacher_id = ? LIMIT 1",
        (int(course_id), int(teacher_id)),
    ).fetchone()
    if has_offering:
        return dict(course)
    has_sync = conn.execute(
        "SELECT 1 FROM teacher_academic_course_sync_items WHERE course_id = ? AND teacher_id = ? LIMIT 1",
        (int(course_id), int(teacher_id)),
    ).fetchone()
    if has_sync:
        return dict(course)
    raise HTTPException(403, "你没有该课程的操作权限")


def _load_owned_pack(conn, pack_id: int, teacher_id: int) -> dict[str, Any]:
    pack = pack_service.get_pack(conn, int(pack_id))
    if pack is None or pack.get("status") == "archived":
        raise HTTPException(404, "学习文档包不存在或已归档")
    if int(pack["teacher_id"]) != int(teacher_id):
        raise HTTPException(403, "只能操作自己的学习文档包")
    return pack


def _load_textbook_brief(conn, *, course_id: int, teacher_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT tb.title, tb.publisher, tb.authors_json
        FROM textbooks tb
        JOIN class_offerings o ON o.textbook_id = tb.id
        WHERE o.course_id = ? AND o.teacher_id = ?
        ORDER BY o.id DESC LIMIT 1
        """,
        (int(course_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        return {}
    import json as _json

    try:
        authors = _json.loads(row["authors_json"] or "[]")
    except (ValueError, TypeError):
        authors = []
    return {
        "title": row["title"],
        "author": "、".join(str(a) for a in authors[:3]),
        "publisher": row["publisher"] or "",
    }


def _pack_summary(conn, pack: dict[str, Any]) -> dict[str, Any]:
    lessons = pack_service.list_pack_lessons(conn, pack["id"])
    ready = sum(1 for l in lessons if l["gen_status"] == "ready")
    total = sum(1 for l in lessons if l["gen_status"] != "excluded")
    root_row = conn.execute(
        "SELECT material_path FROM course_materials WHERE id = ? LIMIT 1",
        (int(pack["root_material_id"]),),
    ).fetchone()
    return {
        "id": pack["id"],
        "course_id": pack["course_id"],
        "root_material_id": pack["root_material_id"],
        "root_material_path": root_row["material_path"] if root_row else "",
        "theme": pack["theme"],
        "spec_version": pack["spec_version"],
        "status": pack["status"],
        "ready_count": ready,
        "total_count": total,
        "updated_at": pack["updated_at"],
        "render_shell_url": f"/materials/render-view/{int(pack['root_material_id'])}",
        # 引擎版本治理(R5):指纹不一致 → 管理面板高亮「刷新引擎」
        "assets_outdated": str(pack.get("assets_fingerprint") or "")
        != lessondoc_assets.assets_fingerprint(),
        "lessons": lessons,
    }


def _build_manifest_from_request(
    conn, *, course: dict[str, Any], teacher_id: int, req: CreatePackRequest
) -> dict[str, Any]:
    included = [l for l in req.lessons if not l.excluded]
    if not included:
        raise HTTPException(400, "至少保留一个课次")
    lessons_payload = [
        {
            "n": l.n,
            "title": l.title.strip() or f"第{l.n}次课",
            "lab": bool(l.lab),
            "topics": [t for t in (l.topics or []) if str(t).strip()][:6],
            "status": "pending",
            "userHint": l.userHint.strip(),
        }
        for l in sorted(req.lessons, key=lambda x: x.n)
        if not l.excluded
    ]
    stages = [
        {"label": s.label.strip() or "阶段", "lessons": [n for n in s.lessons]}
        for s in req.stages
        if s.lessons
    ]
    total_hours = int(course.get("total_hours") or 0)
    manifest: dict[str, Any] = {
        "spec": spec.SPEC_VERSION,
        "kind": spec.DOC_KIND_HOME,
        "theme": req.theme,
        "course": {
            "name": course.get("name") or "",
            "code": course.get("academic_course_code") or "",
            "credits": course.get("credits"),
            "totalHours": total_hours or None,
            "sessionCount": len(lessons_payload),
            "perSessionSections": int(req.per_session_sections or 2),
            "intro": req.intro.strip() or (course.get("description") or "")[:120],
        },
        "textbook": _load_textbook_brief(conn, course_id=int(course["id"]), teacher_id=teacher_id),
        "stages": stages,
        "lessons": lessons_payload,
        "conventions": {
            "submit": "作业/实验报告一律在 lanshare 平台完成提交",
            "courseHint": req.course_hint.strip(),
        },
    }
    return manifest


# ---------------------------------------------------------------- 端点

@router.post("/api/lessondoc/packs", response_class=JSONResponse)
async def create_lessondoc_pack(
    payload: CreatePackRequest,
    user: dict = Depends(get_current_teacher),
):
    excluded_nos = [l.n for l in payload.lessons if l.excluded]
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        course = _ensure_course_access(conn, payload.course_id, user["id"])
        manifest = _build_manifest_from_request(
            conn, course=course, teacher_id=int(user["id"]), req=payload
        )
        try:
            result = pack_service.create_pack_skeleton(
                conn,
                teacher_id=int(user["id"]),
                course_id=int(course["id"]),
                manifest=manifest,
                theme=payload.theme,
                pack_name=payload.pack_name.strip() or None,
            )
        except (LessonDocPackError, LessonDocValidationError) as exc:
            conn.rollback()
            raise HTTPException(400, str(exc))
        pack = result["pack"]
        for n in excluded_nos:
            pack_service.update_lesson_state(
                conn, pack_id=pack["id"], lesson_no=int(n), gen_status="excluded"
            )
        conn.commit()

        # 生成范围:first2 = 前 2 个未排除课次;all = 全部;none = 仅骨架
        target_nos: list[int] = []
        if payload.generate_scope in {"first2", "all"}:
            included = [l["lesson_no"] for l in pack_service.list_pack_lessons(conn, pack["id"])
                        if l["gen_status"] == "pending"]
            target_nos = included[:2] if payload.generate_scope == "first2" else included
        summary = _pack_summary(conn, pack)

    if target_nos:
        asyncio.create_task(
            lessondoc_generate.run_lessondoc_batch(
                pack_id=int(pack["id"]), lesson_nos=target_nos, teacher_id=int(user["id"])
            )
        )
    return {
        "status": "success",
        "message": f"学习文档包已创建{'，正在生成 ' + str(len(target_nos)) + ' 个课次' if target_nos else ''}",
        "pack": summary,
        "warnings": result["warnings"],
        "generating_lessons": target_nos,
    }


@router.get("/api/lessondoc/packs", response_class=JSONResponse)
async def list_lessondoc_packs(
    course_id: int = Query(..., ge=1),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        packs = pack_service.list_packs_for_course(
            conn, course_id=int(course_id), teacher_id=int(user["id"])
        )
        return {"status": "success", "packs": [_pack_summary(conn, p) for p in packs]}


@router.get("/api/lessondoc/classrooms/{class_offering_id}/pack", response_class=JSONResponse)
async def get_lessondoc_pack_for_classroom(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    """课堂视角:本课堂绑定的是不是 LessonDoc 包?各课次就绪度如何?

    课堂页据此把「AI 生成材料」分流为「AI 重写本课 / AI 生成下次课」。
    未绑定 LessonDoc 包时返回 pack=null,前端维持原有生成逻辑。
    """
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT teacher_id FROM class_offerings WHERE id = ? LIMIT 1",
            (int(class_offering_id),),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "课堂不存在")
        if int(row["teacher_id"]) != int(user["id"]):
            raise HTTPException(403, "只能查看自己的课堂")
        pack = pack_service.find_pack_for_offering(conn, class_offering_id=int(class_offering_id))
        if pack is None or int(pack["teacher_id"]) != int(user["id"]):
            return {"status": "success", "pack": None}
        summary = _pack_summary(conn, pack)
        next_pending = next(
            (l["lesson_no"] for l in summary["lessons"] if l["gen_status"] == "pending"), None
        )
        summary["next_pending_lesson"] = next_pending
        return {"status": "success", "pack": summary}


@router.get("/api/lessondoc/packs/by-root/{root_material_id}", response_class=JSONResponse)
async def get_lessondoc_pack_by_root(
    root_material_id: int,
    user: dict = Depends(get_current_teacher),
):
    """按包根材料反查 pack(壳页「改这一页」入口用:壳页只知道 nodeId)。

    非 lessondoc 包返回 pack=null(旧手写包没有登记行),前端据此隐藏入口。
    """
    with get_db_connection() as conn:
        pack = pack_service.get_pack_by_root(conn, int(root_material_id))
        if pack is None or pack.get("status") != "active":
            return {"status": "success", "pack": None}
        if int(pack["teacher_id"]) != int(user["id"]):
            return {"status": "success", "pack": None}
        return {"status": "success", "pack": {"id": pack["id"], "theme": pack["theme"]}}


@router.get("/api/lessondoc/packs/{pack_id}", response_class=JSONResponse)
async def get_lessondoc_pack(
    pack_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        pack = _load_owned_pack(conn, pack_id, user["id"])
        summary = _pack_summary(conn, pack)
        try:
            summary["manifest"] = pack_service.read_manifest(conn, pack)
        except LessonDocPackError:
            summary["manifest"] = pack.get("manifest_cache") or {}
        return {"status": "success", "pack": summary}


@router.put("/api/lessondoc/packs/{pack_id}/theme", response_class=JSONResponse)
async def update_lessondoc_pack_theme(
    pack_id: int,
    payload: ThemeRequest,
    user: dict = Depends(get_current_teacher),
):
    theme = payload.theme.strip().lower()
    parts = [p for p in theme.replace("+", " ").split() if p]
    if not any(p in spec.THEMES for p in parts):
        raise HTTPException(400, f"未知主题:{payload.theme}")
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        pack = _load_owned_pack(conn, pack_id, user["id"])
        manifest = pack_service.read_manifest(conn, pack)
        manifest["theme"] = theme
        warnings = pack_service.write_manifest(conn, pack, manifest)
        pack_service.touch_pack(conn, pack["id"], theme=theme)
        conn.commit()
    return {"status": "success", "message": "默认主题已更新", "warnings": warnings}


class StagesRequest(BaseModel):
    stages: list[StageItem]


@router.put("/api/lessondoc/packs/{pack_id}/stages", response_class=JSONResponse)
async def update_lessondoc_pack_stages(
    pack_id: int,
    payload: StagesRequest,
    user: dict = Depends(get_current_teacher),
):
    """编辑阶段分组(R3):替换 manifest.stages 并重渲首页。

    validate_manifest 兜底:未覆盖的课次自动归入「其他课次」并告警;
    传空数组 = 恢复单一「全部课次」分组。
    """
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        pack = _load_owned_pack(conn, pack_id, user["id"])
        manifest = pack_service.read_manifest(conn, pack)
        manifest["stages"] = [
            {"label": s.label.strip() or "阶段", "lessons": list(s.lessons)}
            for s in payload.stages
            if s.lessons
        ]
        try:
            warnings = pack_service.write_manifest(conn, pack, manifest)
        except LessonDocValidationError as exc:
            conn.rollback()
            raise HTTPException(400, str(exc))
        conn.commit()
    return {"status": "success", "message": "阶段分组已更新，课程首页已重渲", "warnings": warnings}


@router.put("/api/lessondoc/packs/{pack_id}/lessons/{lesson_no}", response_class=JSONResponse)
async def update_lessondoc_lesson(
    pack_id: int,
    lesson_no: int,
    payload: UpdateLessonRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        pack = _load_owned_pack(conn, pack_id, user["id"])
        lessons = {l["lesson_no"]: l for l in pack_service.list_pack_lessons(conn, pack["id"])}
        state = lessons.get(int(lesson_no))
        if state is None:
            raise HTTPException(404, "该课次不在此学习文档包中")
        gen_status = None
        if payload.excluded is not None:
            if state["gen_status"] in {"queued", "running"}:
                raise HTTPException(409, "课次正在生成中,无法调整排除状态")
            gen_status = "excluded" if payload.excluded else (
                "ready" if state["gen_status"] == "ready" else "pending"
            )
        pack_service.update_lesson_state(
            conn,
            pack_id=pack["id"],
            lesson_no=int(lesson_no),
            gen_status=gen_status,
            user_hint=payload.user_hint,
        )
        if payload.title is not None and payload.title.strip():
            manifest = pack_service.read_manifest(conn, pack)
            for lesson in manifest.get("lessons") or []:
                if isinstance(lesson, dict) and int(lesson.get("n") or 0) == int(lesson_no):
                    lesson["title"] = payload.title.strip()
            pack_service.write_manifest(conn, pack, manifest)
        conn.commit()
    return {"status": "success", "message": "课次设置已更新"}


@router.post("/api/lessondoc/packs/{pack_id}/lessons/{lesson_no}/generate", response_class=JSONResponse)
async def generate_lessondoc_lesson(
    pack_id: int,
    lesson_no: int,
    payload: GenerateLessonRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        pack = _load_owned_pack(conn, pack_id, user["id"])
        lessons = {l["lesson_no"]: l for l in pack_service.list_pack_lessons(conn, pack["id"])}
        state = lessons.get(int(lesson_no))
        if state is None:
            raise HTTPException(404, "该课次不在此学习文档包中")
        if state["gen_status"] == "excluded":
            raise HTTPException(409, "该课次已被排除,请先在课次设置中恢复")
        task = lessondoc_generate.create_lessondoc_task(
            conn,
            pack=pack,
            lesson_no=int(lesson_no),
            mode=payload.mode,
            user_hint=payload.user_hint,
            class_offering_id=int(payload.class_offering_id or 0),
            session_id=int(payload.session_id or 0),
        )
        conn.commit()
    if not task.get("already_running"):
        asyncio.create_task(
            lessondoc_generate.run_lessondoc_task(
                int(pack["id"]),
                int(lesson_no),
                mode=payload.mode,
                user_hint=payload.user_hint,
            )
        )
    return {
        "status": "success",
        "message": "已在队列中,助教正在思考…" if task.get("already_running") else "生成任务已提交",
        "task": task,
    }


class SlideRewriteRequest(BaseModel):
    user_hint: str = Field(default="", max_length=3000)


@router.post(
    "/api/lessondoc/packs/{pack_id}/lessons/{lesson_no}/slides/{slide_no}/rewrite",
    response_class=JSONResponse,
)
async def rewrite_lessondoc_slide(
    pack_id: int,
    lesson_no: int,
    slide_no: int,
    payload: SlideRewriteRequest,
    user: dict = Depends(get_current_teacher),
):
    """单页重写(R2):同步执行,教师改完立即刷新即见。slide_no 与页码一致(1 起)。"""
    with get_db_connection() as conn:
        _load_owned_pack(conn, pack_id, user["id"])
    result = await lessondoc_generate.rewrite_slide_with_ai(
        pack_id=int(pack_id),
        lesson_no=int(lesson_no),
        slide_no=int(slide_no),
        user_hint=payload.user_hint,
    )
    warnings = result.get("warnings") or []
    return {
        "status": "success",
        "message": f"第 {slide_no} 页已重写"
        + (f"({len(warnings)} 处内容被降级,详见告警)" if warnings else ""),
        "warnings": warnings,
    }


@router.post("/api/lessondoc/packs/{pack_id}/generate-batch", response_class=JSONResponse)
async def generate_lessondoc_batch(
    pack_id: int,
    payload: BatchGenerateRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        pack = _load_owned_pack(conn, pack_id, user["id"])
        lessons = pack_service.list_pack_lessons(conn, pack["id"])
        candidates = [
            l["lesson_no"]
            for l in lessons
            if l["gen_status"] in {"pending", "failed"}
            and (not payload.lesson_nos or l["lesson_no"] in set(payload.lesson_nos))
        ]
        if payload.limit:
            candidates = candidates[: payload.limit]
    if not candidates:
        return {"status": "success", "message": "没有待生成的课次", "lesson_nos": []}
    asyncio.create_task(
        lessondoc_generate.run_lessondoc_batch(
            pack_id=int(pack["id"]), lesson_nos=candidates, teacher_id=int(user["id"])
        )
    )
    return {
        "status": "success",
        "message": f"已排队顺序生成 {len(candidates)} 个课次(前课完成后自动衔接下一课)",
        "lesson_nos": candidates,
    }


@router.post("/api/lessondoc/packs/{pack_id}/bind", response_class=JSONResponse)
async def bind_lessondoc_pack(
    pack_id: int,
    payload: BindRequest,
    user: dict = Depends(get_current_teacher),
):
    if not payload.class_offering_ids:
        raise HTTPException(400, "请选择要绑定的课堂")
    from ..services.html_package_service import (
        apply_package_session_bindings,
        parse_html_package,
    )

    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        pack = _load_owned_pack(conn, pack_id, user["id"])
        for offering_id in payload.class_offering_ids:
            row = conn.execute(
                "SELECT teacher_id FROM class_offerings WHERE id = ? LIMIT 1",
                (int(offering_id),),
            ).fetchone()
            if row is None or int(row["teacher_id"]) != int(user["id"]):
                raise HTTPException(403, f"课堂 {offering_id} 不存在或不属于你")
        root_row = conn.execute(
            "SELECT * FROM course_materials WHERE id = ? LIMIT 1",
            (int(pack["root_material_id"]),),
        ).fetchone()
        if root_row is None:
            raise HTTPException(410, "学习文档包的包根材料不存在")
        package = parse_html_package(conn, root_row)
        if package is None:
            raise HTTPException(
                409, "包内暂无可绑定内容(需要 main.html 和至少一个已生成课次)"
            )
        result = apply_package_session_bindings(
            conn,
            package=package,
            offering_ids=[int(x) for x in payload.class_offering_ids],
            teacher_id=int(user["id"]),
        )
        conn.commit()
    return {"status": "success", "binding": result}


@router.post("/api/lessondoc/packs/import-legacy", response_class=JSONResponse)
async def import_legacy_package(
    payload: ImportLegacyRequest,
    user: dict = Depends(get_current_teacher),
):
    """把手写 HTML 包升级为 LessonDoc 配置驱动包。

    **原包纹丝不动**：抽取结果落到一个新包里，教师对比满意后自行删旧包。
    抽取允许有损（stepper 解说词、阶段分组等抽不回来），逐条告警返回。
    `dry_run=true` 时只解析看告警，不落库。
    """
    from ..services.html_package_service import find_html_package_root, load_material_file_text
    from ..services.lessondoc import legacy_import

    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        course = _ensure_course_access(conn, payload.course_id, user["id"])
        root_row = conn.execute(
            "SELECT * FROM course_materials WHERE id = ? AND teacher_id = ? LIMIT 1",
            (int(payload.root_material_id), int(user["id"])),
        ).fetchone()
        if root_row is None:
            raise HTTPException(404, "源材料不存在或不属于你")
        package = find_html_package_root(conn, root_row)
        if package is None:
            raise HTTPException(
                409, "这个文件夹不是可识别的 HTML 学习文档包（需要 main.html 和至少一个 lesson_N）"
            )
        if pack_service.get_pack_by_root(conn, int(package["root_node_id"])):
            raise HTTPException(409, "这个包已经是 LessonDoc 配置驱动包，无需迁移")

        warnings: list[str] = []
        decks: list[tuple[int, dict[str, Any]]] = []
        lessons_meta: list[dict[str, Any]] = []
        for lesson in package.get("lessons") or []:
            lesson_no = int(lesson["number"])
            raw = load_material_file_text(conn, lesson["entry"])
            if not raw:
                warnings.append(f"第 {lesson_no} 课的入口文件读不到内容，已跳过")
                continue
            try:
                deck, deck_warnings = legacy_import.extract_deck_from_legacy_html(
                    raw, lesson_no=lesson_no, course_name=course.get("name") or ""
                )
            except ValueError as exc:
                warnings.append(f"第 {lesson_no} 课迁移失败：{exc}")
                continue
            warnings.extend(f"第 {lesson_no} 课 · {w}" for w in deck_warnings)
            decks.append((lesson_no, deck))
            lessons_meta.append(
                {
                    "n": lesson_no,
                    "title": deck.get("title") or f"第{lesson_no}次课",
                    "topics": [],
                    "status": "ready",
                }
            )
        if not decks:
            raise HTTPException(422, "没有任何课次能被解析，迁移已取消")

        home_raw = load_material_file_text(conn, package["main_entry"]) or ""
        try:
            manifest, manifest_warnings = legacy_import.extract_manifest_from_legacy_home(
                home_raw, lessons=lessons_meta, course_name=course.get("name") or ""
            )
        except ValueError as exc:
            raise HTTPException(422, f"课程首页迁移失败：{exc}")
        warnings.extend(f"首页 · {w}" for w in manifest_warnings)

        if payload.dry_run:
            conn.rollback()
            return {
                "status": "success",
                "dry_run": True,
                "lesson_count": len(decks),
                "warnings": warnings,
                "preview": {"course": manifest.get("course"), "lessons": lessons_meta},
            }

        try:
            result = pack_service.create_pack_skeleton(
                conn,
                teacher_id=int(user["id"]),
                course_id=int(course["id"]),
                manifest=manifest,
                theme=payload.theme,
                pack_name=payload.pack_name.strip() or None,
            )
        except (LessonDocPackError, LessonDocValidationError) as exc:
            conn.rollback()
            raise HTTPException(400, str(exc))
        pack = result["pack"]
        warnings.extend(result["warnings"])

        for lesson_no, deck in decks:
            try:
                lesson_warnings = pack_service.write_lesson_files(conn, pack, lesson_no, deck)
            except LessonDocValidationError as exc:
                warnings.append(f"第 {lesson_no} 课写入失败：{exc}")
                pack_service.update_lesson_state(
                    conn, pack_id=pack["id"], lesson_no=lesson_no, gen_status="failed",
                    warnings=[str(exc)],
                )
                continue
            warnings.extend(f"第 {lesson_no} 课 · {w}" for w in lesson_warnings)
            pack_service.update_lesson_state(
                conn, pack_id=pack["id"], lesson_no=lesson_no, gen_status="ready",
                warnings=lesson_warnings,
            )
        conn.commit()
        summary = _pack_summary(conn, pack)

    return {
        "status": "success",
        "message": f"已迁移 {len(decks)} 个课次到新的学习文档包（原包保持不变）",
        "pack": summary,
        "warnings": warnings,
    }


@router.post("/api/lessondoc/packs/{pack_id}/refresh-assets", response_class=JSONResponse)
async def refresh_lessondoc_assets(
    pack_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        pack = _load_owned_pack(conn, pack_id, user["id"])
        try:
            updated = pack_service.refresh_pack_assets(conn, pack)
        except LessonDocPackError as exc:
            conn.rollback()
            raise HTTPException(409, str(exc))
        conn.commit()
    return {"status": "success", "message": f"包内引擎已刷新({updated} 个文件)"}
