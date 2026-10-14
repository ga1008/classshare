import os
import threading

from .common import *


router = APIRouter()

# Formats that are already compressed: storing them saves CPU and changes nothing in size.
_ZIP_STORED_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".mp4", ".mov", ".mp3", ".m4a",
    ".zip", ".rar", ".7z", ".gz", ".tgz", ".bz2", ".xz", ".pdf", ".docx", ".xlsx", ".pptx",
})


def _safe_archive_relative_path(raw: str) -> str:
    """Archive member path without absolute roots or ``..`` (no zip-slip on extraction)."""
    parts = [part for part in str(raw or "").replace("\\", "/").split("/") if part not in ("", ".", "..")]
    return "/".join(parts) or "file"


_EXPORT_SLOTS = threading.BoundedSemaphore(2)
_EXPORT_STALE_SECONDS = 3600


def _export_temp_dir() -> Path:
    """Dedicated scratch dir; files a crash or disconnect left behind are swept."""
    import tempfile
    import time

    directory = Path(tempfile.gettempdir()) / "lanshare-exports"
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _EXPORT_STALE_SECONDS
    for stale in directory.glob("lanshare-export-*.zip"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass
    return directory


def _remove_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@router.get("/assignments/{assignment_id}/export-attachments/{class_offering_id}")
def export_submission_attachments(
    assignment_id: str,
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    """将指定作业所有已提交学生的附件打包为 zip 下载。
    目录结构: 班级名-作业名/学生姓名-学号/原始附件文件
    """
    with get_db_connection() as conn:
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

        offering = conn.execute(
            "SELECT * FROM class_offerings WHERE id = ?", (class_offering_id,)
        ).fetchone()
        if not offering:
            raise HTTPException(404, "未找到班级课堂")
        if int(assignment.get("class_offering_id") or 0) != int(class_offering_id):
            raise HTTPException(400, "作业与当前班级课堂不匹配")

        class_ids = offering_class_ids(conn, int(offering["id"])) or [int(offering["class_id"])]
        class_name = (
            str(offering["combined_class_names"] or "").strip()
            if "combined_class_names" in offering.keys()
            else ""
        )
        if not class_name:
            class_info = conn.execute(
                "SELECT name FROM classes WHERE id = ?", (offering["class_id"],)
            ).fetchone()
            if not class_info:
                raise HTTPException(404, "未找到班级")
            class_name = str(class_info["name"] or "").strip()

        # 获取所有已提交学生的附件记录
        class_placeholders = ",".join("?" for _ in class_ids)
        rows = conn.execute(
            f"""
            SELECT sf.stored_path, sf.relative_path, sf.original_filename,
                   s.student_pk_id, stu.name AS student_name,
                   stu.student_id_number,
                   cls.name AS student_class_name
            FROM submission_files sf
            JOIN submissions s ON s.id = sf.submission_id
            JOIN students stu ON stu.id = s.student_pk_id
            LEFT JOIN classes cls ON cls.id = stu.class_id
            WHERE s.assignment_id = ?
              AND s.student_pk_id IN (
                  SELECT id
                  FROM students
                  WHERE class_id IN ({class_placeholders})
                    AND COALESCE(enrollment_status, 'active') = 'active'
              )
              AND s.status != 'unsubmitted'
            ORDER BY stu.student_id_number, sf.relative_path
            """,
            (assignment_id, *class_ids),
        ).fetchall()

    if not rows:
        raise HTTPException(404, "当前没有已提交附件可供导出")

    # Build the zip in a temporary file, not in memory: a class of large
    # submissions would otherwise hold hundreds of MB per request (audit B2).
    assignment_title = str(assignment.get("title") or "").strip()
    # Sanitize folder names for cross-platform compatibility
    root_folder = _sanitize_zip_path(f"{class_name}-{assignment_title}")
    import tempfile

    from starlette.background import BackgroundTask

    # At most two zips are built at once: each holds a threadpool worker and disk.
    if not _EXPORT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "附件打包任务较多，请稍后再试")
    try:
        handle = tempfile.NamedTemporaryFile(prefix="lanshare-export-", suffix=".zip", delete=False,
                                             dir=_export_temp_dir())
        zip_path = handle.name
        handle.close()
        try:
            _write_attachment_zip(zip_path, rows, root_folder=root_folder, multi_class=len(class_ids) > 1)
        except Exception:
            _remove_quietly(zip_path)
            raise
    finally:
        _EXPORT_SLOTS.release()

    zip_filename = f"{root_folder}.zip"
    encoded_filename = quote(zip_filename)
    return FileResponse(
        zip_path,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
        background=BackgroundTask(_remove_quietly, zip_path),
    )


def _write_attachment_zip(zip_path: str, rows, *, root_folder: str, multi_class: bool) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Map student_pk_id -> unique folder name (handles duplicate names)
        student_folder_map: dict[int, str] = {}
        used_folder_names: set[str] = set()

        for row in rows:
            resolved_path = resolve_submission_file_path(str(row["stored_path"] or ""))
            if not resolved_path:
                continue
            stored_path = Path(resolved_path)
            if not stored_path.is_file():
                continue  # a missing file must not fail the whole class export

            student_pk_id = int(row["student_pk_id"])

            # Resolve folder name once per student；合班时按行政班分一级目录
            if student_pk_id not in student_folder_map:
                student_name = str(row["student_name"] or "").strip() or "未知"
                student_id_number = str(row["student_id_number"] or "").strip() or "无学号"
                folder = _sanitize_zip_path(f"{student_name}-{student_id_number}")
                if multi_class:
                    class_folder = _sanitize_zip_path(
                        str(row["student_class_name"] or "").strip() or "未分班"
                    )
                    folder = f"{class_folder}/{folder}"

                if folder in used_folder_names:
                    base = folder
                    idx = 2
                    while f"{base}_{idx}" in used_folder_names:
                        idx += 1
                    folder = f"{base}_{idx}"
                used_folder_names.add(folder)
                student_folder_map[student_pk_id] = folder

            student_folder = student_folder_map[student_pk_id]

            # Use relative_path to preserve sub-directory structure if any
            relative_path = _safe_archive_relative_path(row["relative_path"] or row["original_filename"] or "file")
            arc_path = f"{root_folder}/{student_folder}/{relative_path}"
            compression = (zipfile.ZIP_STORED if stored_path.suffix.lower() in _ZIP_STORED_EXTENSIONS
                           else zipfile.ZIP_DEFLATED)
            zf.write(stored_path, arc_path, compress_type=compression)


def _rows_as_value_tuples(cursor) -> list[tuple]:
    """Engine-neutral rows for pandas: psycopg dict rows would otherwise be
    re-indexed by DataFrame(columns=...) into an all-NaN frame."""
    rows = cursor.fetchall()
    return [
        tuple(row.values()) if isinstance(row, dict) else tuple(row)
        for row in rows
    ]


@router.get("/assignments/{assignment_id}/export/{class_offering_id}", response_class=FileResponse)
def export_grades_for_class(assignment_id: str, class_offering_id: int, user: dict = Depends(get_current_teacher)):
    """V4.0: 导出此作业在指定班级课堂的成绩"""
    with get_db_connection() as conn:
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

        # 通过 class_offering_id 解析出实际的 class_id
        offering = conn.execute("SELECT * FROM class_offerings WHERE id = ?", (class_offering_id,)).fetchone()
        if not offering:
            raise HTTPException(404, "未找到班级课堂")
        if int(assignment.get("class_offering_id") or 0) != int(class_offering_id):
            raise HTTPException(400, "作业与当前班级课堂不匹配")
        class_id = offering['class_id']
        class_ids = offering_class_ids(conn, int(offering["id"])) or [int(class_id)]
        class_placeholders = ",".join("?" for _ in class_ids)

        class_info = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not class_info:
            raise HTTPException(404, "未找到班级")

        # 1. 获取课堂全部班级的学生
        roster_cursor = conn.execute(
            f"""
            SELECT s.id, s.student_id_number, s.name, cls.name AS class_name
            FROM students s
            LEFT JOIN classes cls ON cls.id = s.class_id
            WHERE s.class_id IN ({class_placeholders})
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY cls.name, s.student_id_number, s.id
            """,
            tuple(class_ids),
        )
        roster_df = pd.DataFrame(
            _rows_as_value_tuples(roster_cursor),
            columns=['student_pk_id', '学号', '姓名', '班级'],
        )
        # 单班课堂保持原有列结构；合班时保留「班级」列并按班排序方便拆分。
        if len(class_ids) <= 1:
            roster_df = roster_df.drop(columns=['班级'])

        if roster_df.empty:
            raise HTTPException(404, "此班级没有学生，无法导出。")

        # 2. 获取这些学生的此项作业成绩
        grades_cursor = conn.execute(
            f"""SELECT student_pk_id,
                      student_name,
                      score,
                      CASE
                          WHEN COALESCE(is_absence_score, 0) = 1 THEN '未提交（缺交记0）'
                          ELSE status
                      END AS status,
                      feedback_md
               FROM submissions
                WHERE assignment_id = ?
                  AND student_pk_id IN (
                      SELECT id
                      FROM students
                      WHERE class_id IN ({class_placeholders})
                        AND COALESCE(enrollment_status, 'active') = 'active'
                  )""",
            (assignment_id, *class_ids)
        )
        grades_df = pd.DataFrame(
            _rows_as_value_tuples(grades_cursor),
            columns=['student_pk_id', '提交姓名', '分数', '状态', '评语'],
        )

    final_df = roster_df.merge(grades_df, on='student_pk_id', how='left')

    export_class_label = str(class_info['name'] or '').strip()
    if len(class_ids) > 1 and "combined_class_names" in offering.keys() and str(offering["combined_class_names"] or "").strip():
        export_class_label = str(offering["combined_class_names"]).strip()
    return build_grades_export_response(
        final_df,
        class_label=export_class_label,
        assignment_title=str(assignment['title'] or ''),
    )


XLSX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def build_grades_export_response(final_df, *, class_label: str, assignment_title: str) -> StreamingResponse:
    """Stream the grade workbook from memory.

    The title is teacher-entered text: it must never become a filesystem path
    (``/`` or ``..`` used to escape the assignment directory or 500), and
    concurrent exports must not overwrite a shared file left on disk.
    """
    import re

    stem = re.sub(r"\.{2,}", ".", _sanitize_zip_path(f"成绩_{class_label}_{assignment_title}")).strip(". ")
    export_filename = f"{stem or '成绩'}.xlsx"
    buffer = io.BytesIO()
    final_df.to_excel(buffer, index=False)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(export_filename)}",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/assignments/{assignment_id}/export-review-docx")
def export_student_assignment_review_docx(
    assignment_id: str,
    user: dict = Depends(get_current_student),
):
    """导出学生本人已批改作业/考试的打印复习 Word。"""
    with get_db_connection() as conn:
        export = build_student_submission_export_docx(
            conn,
            assignment_id=assignment_id,
            student_pk_id=int(user["id"]),
        )
        conn.commit()

    encoded_filename = quote(export.filename)
    return StreamingResponse(
        io.BytesIO(export.content),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Cache-Control": "private, no-store",
        },
    )
