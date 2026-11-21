"""Teacher-owned course packages and classroom-scoped project topic distribution."""
import csv
import io
import json
from html import escape
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user, get_current_user_optional
from ..services import project_topic_dispatch_service as dispatches
from ..services import project_topic_package_service as packages
from ..services.document_render_service import DocumentRenderError, document_render_service
from ..services.material_render_grants import issue_topic_render_grant, resolve_topic_render_grant
from ..services.resource_access_service import ensure_classroom_access


def _private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/api/classrooms/{offering_id}/project-topics", dependencies=[Depends(_private_response)])
_FILE_HEADERS = {
    "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox allow-scripts allow-downloads; default-src 'none'; "
                               "img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                               "script-src 'self' 'unsafe-inline'; font-src 'self' data:; "
                               "media-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'",
}
_EXPORT_COLUMNS = {
    "class_name": "班级", "course_name": "课程", "dispatch_title": "派题活动", "package_version": "题包版本",
    "group_index": "组号", "group_name": "组名", "topic_number": "题号", "topic_title": "题目",
    "student_number": "学号", "student_name": "姓名", "membership": "分组状态",
}


def _activity_row(conn, offering_id, activity_id):
    row = conn.execute(
        "SELECT revision FROM project_topic_dispatches WHERE id = ? AND class_offering_id = ?",
        (activity_id, offering_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "本课堂没有这次派题活动")
    return row


@router.get("/packages")
def list_topic_packages(offering_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, offering_id, user)
        return {"packages": packages.list_teacher_packages(conn, user["id"])}


@router.post("/packages/import")
def import_topic_package(offering_id: int, files: list[UploadFile] = File(...), paths: str = Form(""),
                         title: str = Form(""), user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, offering_id, user)
        if len(files) > packages.MAX_FILES:
            raise HTTPException(413, f"题包最多可导入 {packages.MAX_FILES} 个文件")
        try:
            names = json.loads(paths) if paths else [file.filename for file in files]
        except ValueError:
            raise HTTPException(400, "上传文件路径清单格式错误")
        if (not isinstance(names, list) or len(files) != len(names) or
                any(not isinstance(name, str) or not name for name in names)):
            raise HTTPException(400, "上传文件与相对路径不匹配")
        entries = []
        total = 0
        for name, file in zip(names, files):
            is_archive = len(files) == 1 and name.lower().endswith(".zip")
            limit = packages.MAX_UPLOAD_BYTES if is_archive else packages.MAX_FILE_BYTES
            remaining = min(limit, packages.MAX_TOTAL_BYTES - total)
            if file.size is not None and file.size > remaining:
                raise HTTPException(413, "题包文件大小超过导入上限")
            content = file.file.read(remaining + 1)
            if len(content) > remaining:
                raise HTTPException(413, "题包文件大小超过导入上限")
            total += len(content)
            entries.append((name, content))
        package = packages.import_package(conn, user["id"], entries, title=title)
        conn.commit()
        return {"package": package}


@router.get("/packages/{package_id}")
def get_topic_package(offering_id: int, package_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, offering_id, user)
        return {"package": packages.get_teacher_package(conn, package_id, user["id"])}


@router.post("/packages/{package_id}/map")
def map_topic_package(offering_id: int, package_id: int, payload: dict, user: dict = Depends(get_current_teacher)):
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        raise HTTPException(400, "请提供题包映射清单")
    with get_db_connection() as conn:
        ensure_classroom_access(conn, offering_id, user)
        package = packages.remap_package(conn, package_id, user["id"], manifest)
        conn.commit()
        return {"package": package}


@router.post("/preview")
def preview_topic_dispatch(offering_id: int, payload: dict, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return {"preview": dispatches.preview_dispatch(conn, offering_id, user, payload)}


@router.get("/packages/{package_id}/dispatch-draft")
def get_topic_dispatch_draft(offering_id: int, package_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        draft = dispatches.get_dispatch_draft(conn, offering_id, user, package_id)
        return {"draft": draft}


@router.put("/packages/{package_id}/dispatch-draft")
def save_topic_dispatch_draft(offering_id: int, package_id: int, payload: dict, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        draft = dispatches.save_dispatch_draft(conn, offering_id, user, package_id, payload)
        conn.commit()
        return {"draft": draft}


@router.post("/activities")
def publish_topic_dispatch(offering_id: int, payload: dict, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        activity = dispatches.publish_dispatch(conn, offering_id, user, payload)
        conn.commit()
        return {"activity": activity}


@router.get("/activities")
def list_topic_dispatches(offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        return {"activities": dispatches.list_dispatches(conn, offering_id, user)}


@router.get("/activities/{activity_id}")
def get_topic_dispatch(offering_id: int, activity_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        _activity_row(conn, offering_id, activity_id)
        return {"activity": dispatches.get_dispatch(conn, activity_id, user)}


@router.post("/activities/{activity_id}/change")
def change_topic_dispatch(offering_id: int, activity_id: int, payload: dict, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _activity_row(conn, offering_id, activity_id)
        activity = dispatches.update_dispatch(conn, activity_id, user, payload)
        conn.commit()
        return {"activity": activity}


def _spreadsheet_text(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _export_rows(offering_id, activity_id, user):
    with get_db_connection() as conn:
        _activity_row(conn, offering_id, activity_id)
        return dispatches.export_dispatch(conn, activity_id, user)


@router.get("/activities/{activity_id}/export.csv")
def export_topic_dispatch(offering_id: int, activity_id: int, user: dict = Depends(get_current_teacher)):
    rows = _export_rows(offering_id, activity_id, user)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(_EXPORT_COLUMNS.values())
    writer.writerows([_spreadsheet_text(row[key]) for key in _EXPORT_COLUMNS] for row in rows)
    return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Cache-Control": "private, no-store",
                             "Content-Disposition": f'attachment; filename="project-{activity_id}.csv"'})


@router.get("/activities/{activity_id}/export.xlsx")
def export_topic_workbook(offering_id: int, activity_id: int, user: dict = Depends(get_current_teacher)):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    rows = _export_rows(offering_id, activity_id, user)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "分组与选题"
    sheet.append(list(_EXPORT_COLUMNS.values()))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for index, width in enumerate((30, 25, 32, 18, 9, 18, 11, 42, 21, 16, 14), 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for index, row in enumerate(rows, 2):
        for col, key in enumerate(_EXPORT_COLUMNS, 1):
            value = row[key]
            cell = sheet.cell(index, col, value)
            if isinstance(value, str):
                cell.data_type = "s"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    with io.BytesIO() as out:
        workbook.save(out)
        content = out.getvalue()
    workbook.close()
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Cache-Control": "private, no-store",
                             "Content-Disposition": f'attachment; filename="project-{activity_id}.xlsx"'})


def _topic_file(offering_id, kind, target_id, topic_key, path, user, download, grant_scope=None):
    with get_db_connection() as conn:
        if kind == "package":
            ensure_classroom_access(conn, offering_id, user)
            if user["role"] != "teacher":
                raise HTTPException(403, "只有教师可以预览题包草稿")
            packages.ensure_teacher_package_owner(conn, target_id, user["id"])
            source, mime = packages.get_scoped_resource(conn, target_id, path, teacher_preview=True)
            revision = target_id
        else:
            revision = _activity_row(conn, offering_id, target_id)["revision"]
            source, mime = dispatches.resolve_dispatch_file(conn, target_id, user, path, topic_key=topic_key)
    scope = {"classroom": offering_id, "kind": kind, "id": target_id, "topic": topic_key, "revision": revision}
    if grant_scope is not None and scope != grant_scope:
        raise HTTPException(403, "资料已修订，请重新打开本组题目")
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    if not download and suffix in {".md", ".txt"}:
        mime = "text/plain"
    if not download and grant_scope is None and suffix in {".htm", ".html"}:
        grant = issue_topic_render_grant(user, scope)
        if grant:
            location = f"/api/classrooms/{offering_id}/project-topics/render/{grant}/{quote(path)}"
            # Keep a stable, authenticated address in the browser. Only the
            # isolated document and its relative assets use the session grant.
            return HTMLResponse(
                '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{escape(name)}</title><style>html,body,iframe{{margin:0;width:100%;height:100%;'
                'border:0;display:block}</style></head><body>'
                f'<iframe title="{escape(name)}" src="{escape(location)}" sandbox="allow-scripts allow-downloads" '
                'referrerpolicy="no-referrer"></iframe></body></html>',
                headers={**_FILE_HEADERS, "Content-Security-Policy": "default-src 'none'; frame-src 'self'; "
                         "style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"},
            )
    if not download and suffix in {".doc", ".docx", ".xls", ".xlsx"}:
        try:
            job = document_render_service.render_artifact(source.read_bytes(), filename=name, media_type=mime)
        except (DocumentRenderError, OSError, RuntimeError) as exc:
            raise HTTPException(503, "文档预览暂不可用，可通过下载原文件阅读") from exc
        source, name, mime = job.root / job.manifest["pdf_file"], str(PurePosixPath(name).with_suffix(".pdf")), "application/pdf"
    headers = dict(_FILE_HEADERS)
    if grant_scope is not None:
        # Local fonts and module requests originate in the opaque, sandboxed document.
        headers["Access-Control-Allow-Origin"] = "null"
    return FileResponse(source, media_type=mime, filename=name, headers=headers,
                        content_disposition_type="attachment" if download else "inline")


@router.get("/packages/{package_id}/files/{path:path}")
def preview_private_topic_file(offering_id: int, package_id: int, path: str, download: bool = False,
                               user: dict = Depends(get_current_teacher)):
    return _topic_file(offering_id, "package", package_id, None, path, user, download)


@router.get("/activities/{activity_id}/files/{path:path}")
def read_common_topic_file(offering_id: int, activity_id: int, path: str, download: bool = False,
                           user: dict = Depends(get_current_user)):
    return _topic_file(offering_id, "activity", activity_id, None, path, user, download)


@router.get("/activities/{activity_id}/topics/{topic_key}/files/{path:path}")
def read_assigned_topic_file(offering_id: int, activity_id: int, topic_key: str, path: str, download: bool = False,
                             user: dict = Depends(get_current_user)):
    return _topic_file(offering_id, "activity", activity_id, topic_key, path, user, download)


@router.get("/render/{grant}/{path:path}", include_in_schema=False)
def render_project_topic_asset(offering_id: int, grant: str, path: str, request: Request, download: bool = False,
                              browser_user: dict | None = Depends(get_current_user_optional)):
    resolved = resolve_topic_render_grant(grant)
    if not resolved:
        raise HTTPException(403, "资料预览已过期，请从课堂重新打开")
    scope, user = resolved
    if scope["classroom"] != offering_id:
        raise HTTPException(403, "课堂与预览授权不符")
    if browser_user:
        if (browser_user["role"], int(browser_user["id"])) != (user["role"], user["id"]):
            raise HTTPException(403, "请从本账号的课堂重新打开资料")
    elif (request.headers.get("sec-fetch-dest", "") in {"", "document", "empty"} or
          (request.headers.get("sec-fetch-dest") == "iframe" and request.headers.get("sec-fetch-site") != "same-origin")):
        raise HTTPException(403, "请登录后从课堂打开资料")
    return _topic_file(offering_id, scope["kind"], scope["id"], scope["topic"], path, user, download, scope)
