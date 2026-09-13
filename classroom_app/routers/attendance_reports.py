"""Teacher-only native attendance archives and review commands."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import config
from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_teacher, require_teacher_domain
from ..services import attendance_report_service as service

api = APIRouter(prefix="/api/attendance-reports", tags=["attendance_reports"])
router = APIRouter()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRefresh(StrictModel):
    year: str
    term: int
    class_offering_id: int | None = None


class BindingCreate(StrictModel):
    source_token: str = Field(min_length=1, max_length=16000)
    class_offering_id: int | None = None


class BindingUpdate(StrictModel):
    expected_revision: int = Field(ge=1)
    class_offering_id: int | None = None


class ExportCreate(StrictModel):
    binding_id: int = Field(gt=0)
    expected_binding_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)


class ParseCreate(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=160)


class ReviewChange(StrictModel):
    target_type: str
    target_id: int = Field(gt=0)
    changes: dict
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=1)


class ConfirmChange(StrictModel):
    expected_run_revision: int = Field(ge=1)
    expected_report_revision: int = Field(ge=1)


class RevisionChange(StrictModel):
    expected_revision: int = Field(ge=1)


@api.get("/source-options")
def source_options(class_offering_id: int | None = None, year: str | None = None, term: int | None = None, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.list_attendance_bindings(conn, user, class_offering_id=class_offering_id, year=year, term=term)


@api.post("/source-options/refresh")
async def refresh_source_options(payload: SourceRefresh, user: dict = Depends(get_current_teacher)):
    if not config.ATTENDANCE_ARCHIVE_ENABLED:
        raise HTTPException(409, "签到归档导入已暂停，仍可查看已有档案")
    from ..services.smart_classroom_attendance_adapter import AttendanceSourceError, list_attendance_source_options
    year, term = service.normalize_attendance_term(payload.year, payload.term)
    if payload.class_offering_id:
        with get_db_connection() as conn:
            service._offering(conn, payload.class_offering_id, user)
    try:
        result = await list_attendance_source_options(int(user["id"]), year, term)
    except AttendanceSourceError as exc:
        raise HTTPException(409 if exc.code in {"missing_credential", "authentication_required", "account_changed"} else 503,
                            {"code": exc.code, "message": str(exc)}) from exc
    items = []
    for item in result["items"]:
        source = {"remote_schedule_id": str(item["id"]), "course_name": str(item.get("course") or ""), "course_code": str(item.get("courseId") or ""),
                  "teaching_class_name": str(item.get("claName") or item.get("chooseCourseNo") or item.get("fullTitle") or ""),
                  "remote_class_id": str(item.get("claId") or ""), "academic_year": year, "academic_term": term,
                  "external_account_key": result["external_account_key"], "credential_id": result.get("credential_id"),
                  "platform_code": result["platform_code"], "school_code": result["school_code"]}
        items.append({**source, "source_token": service.sign_attendance_source_option(int(user["id"]), source)})
    return {"items": items, "year": year, "term": term, "credential_available": True,
            "archive_enabled": config.ATTENDANCE_ARCHIVE_ENABLED, "parse_enabled": config.ATTENDANCE_PARSE_ENABLED}


@api.post("/source-bindings")
def create_binding(payload: BindingCreate, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.save_attendance_binding(conn, user, **payload.model_dump()); conn.commit(); return result


@api.put("/source-bindings/{binding_id}")
def update_binding(binding_id: int, payload: BindingUpdate, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.save_attendance_binding(conn, user, binding_id=binding_id, **payload.model_dump()); conn.commit(); return result


@api.post("/exports", status_code=202)
def export_report(payload: ExportCreate, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.enqueue_attendance_export(conn, user, **payload.model_dump()); conn.commit(); return result


@api.post("/source-bindings/{binding_id}/grade-source")
def select_grade_source(binding_id: int, payload: RevisionChange, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.select_attendance_grade_source(conn, user, binding_id=binding_id, **payload.model_dump()); conn.commit(); return result


@api.get("/options")
def report_options(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.attendance_report_options(conn, user, **dict(request.query_params))


@api.get("")
def reports(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.list_attendance_reports(conn, user, **dict(request.query_params))


@api.get("/jobs/{job_id}")
def job_status(job_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.get_attendance_job(conn, job_id, user)


@api.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.cancel_attendance_job(conn, user, job_id); conn.commit(); return result


@api.get("/{report_id}")
def report_detail(report_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.attendance_report_detail(conn, user, report_id)


@api.api_route("/{report_id}/versions/{version_id}/source.pdf", methods=["GET", "HEAD"])
def source_pdf(report_id: int, version_id: int, download: int = 0, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        path, version = service.attendance_source_file(conn, user, report_id, version_id)
    return FileResponse(path, media_type="application/pdf", filename="点名记录.pdf", content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@api.get("/{report_id}/runs/{run_id}/students")
def run_students(report_id: int, run_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.attendance_run_students(conn, user, report_id, run_id, **dict(request.query_params))


@api.get("/{report_id}/runs/{run_id}/sessions")
def run_sessions(report_id: int, run_id: int, page: int = 1, page_size: int = 20, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.attendance_run_sessions(conn, user, report_id, run_id, page=page, page_size=page_size)


@api.get("/{report_id}/runs/{run_id}/cells")
def run_cells(report_id: int, run_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.attendance_run_cells(conn, user, report_id, run_id, **dict(request.query_params))


@api.get("/{report_id}/runs/{run_id}/reviews")
def run_reviews(report_id: int, run_id: int, page: int = 1, page_size: int = 50, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.attendance_run_reviews(conn, user, report_id, run_id, page=page, page_size=page_size)


@api.post("/{report_id}/versions/{version_id}/parse-runs", status_code=202)
def create_parse(report_id: int, version_id: int, payload: ParseCreate, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.enqueue_attendance_parse(conn, user, report_id=report_id, source_version_id=version_id, **payload.model_dump()); conn.commit(); return result


@api.patch("/{report_id}/runs/{run_id}/review")
def review_run(report_id: int, run_id: int, payload: ReviewChange, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.review_attendance_run(conn, user, report_id=report_id, run_id=run_id, **payload.model_dump()); conn.commit(); return result


@api.post("/{report_id}/runs/{run_id}/confirm")
def confirm_run(report_id: int, run_id: int, payload: ConfirmChange, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.confirm_attendance_run(conn, user, report_id=report_id, run_id=run_id, **payload.model_dump()); conn.commit(); return result


@api.delete("/{report_id}")
def delete_report(report_id: int, payload: RevisionChange, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.set_attendance_report_deleted(conn, user, report_id, deleted=True, **payload.model_dump()); conn.commit(); return result


@api.post("/{report_id}/restore")
def restore_report(report_id: int, payload: RevisionChange, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = service.set_attendance_report_deleted(conn, user, report_id, deleted=False, **payload.model_dump()); conn.commit(); return result


router.include_router(api)


@router.get("/manage/archive/attendance-reports", response_class=HTMLResponse)
@router.get("/manage/archive/attendance-reports/{report_id}", response_class=HTMLResponse)
def attendance_page(request: Request, report_id: int | None = None, class_offering_id: int | None = None,
                    user: dict = Depends(require_teacher_domain("archive"))):
    from .ui_parts.common import _build_manage_template_context
    with get_db_connection() as conn:
        if report_id:
            service.get_attendance_report(conn, report_id, user, allow_deleted=True)
        if class_offering_id:
            service._offering(conn, class_offering_id, user)
    return templates.TemplateResponse(request, "manage/attendance_reports.html", _build_manage_template_context(
        request, user, page_title="签到统计表", active_page="attendance_reports",
        extra={"report_id": report_id, "class_offering_id": class_offering_id,
               "attendance_archive_enabled": config.ATTENDANCE_ARCHIVE_ENABLED,
               "attendance_parse_enabled": config.ATTENDANCE_PARSE_ENABLED}))
