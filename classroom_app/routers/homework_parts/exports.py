from .common import *


router = APIRouter()


@router.get("/assignments/{assignment_id}/export-attachments/{class_offering_id}")
async def export_submission_attachments(
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

        class_id = offering["class_id"]
        class_info = conn.execute(
            "SELECT name FROM classes WHERE id = ?", (class_id,)
        ).fetchone()
        if not class_info:
            raise HTTPException(404, "未找到班级")
        class_name = str(class_info["name"] or "").strip()

        # 获取所有已提交学生的附件记录
        rows = conn.execute(
            """
            SELECT sf.stored_path, sf.relative_path, sf.original_filename,
                   s.student_pk_id, stu.name AS student_name,
                   stu.student_id_number
            FROM submission_files sf
            JOIN submissions s ON s.id = sf.submission_id
            JOIN students stu ON stu.id = s.student_pk_id
            WHERE s.assignment_id = ?
              AND s.student_pk_id IN (
                  SELECT id
                  FROM students
                  WHERE class_id = ?
                    AND COALESCE(enrollment_status, 'active') = 'active'
              )
              AND s.status != 'unsubmitted'
            ORDER BY stu.student_id_number, sf.relative_path
            """,
            (assignment_id, class_id),
        ).fetchall()

    if not rows:
        raise HTTPException(404, "当前没有已提交附件可供导出")

    # Build zip in memory
    assignment_title = str(assignment.get("title") or "").strip()
    # Sanitize folder names for cross-platform compatibility
    root_folder = _sanitize_zip_path(f"{class_name}-{assignment_title}")
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Map student_pk_id -> unique folder name (handles duplicate names)
        student_folder_map: dict[int, str] = {}
        used_folder_names: set[str] = set()

        for row in rows:
            resolved_path = resolve_submission_file_path(str(row["stored_path"] or ""))
            if not resolved_path:
                continue
            stored_path = Path(resolved_path)

            student_pk_id = int(row["student_pk_id"])

            # Resolve folder name once per student
            if student_pk_id not in student_folder_map:
                student_name = str(row["student_name"] or "").strip() or "未知"
                student_id_number = str(row["student_id_number"] or "").strip() or "无学号"
                folder = _sanitize_zip_path(f"{student_name}-{student_id_number}")

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
            relative_path = str(row["relative_path"] or row["original_filename"] or "file")
            arc_path = f"{root_folder}/{student_folder}/{relative_path}"

            zf.write(stored_path, arc_path)

    zip_buffer.seek(0)
    zip_filename = f"{root_folder}.zip"
    encoded_filename = quote(zip_filename)
    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/assignments/{assignment_id}/export/{class_offering_id}", response_class=FileResponse)
async def export_grades_for_class(assignment_id: str, class_offering_id: int, user: dict = Depends(get_current_teacher)):
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

        class_info = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not class_info:
            raise HTTPException(404, "未找到班级")

        # 1. 获取班级所有学生
        roster_cursor = conn.execute(
            """
            SELECT id, student_id_number, name
            FROM students
            WHERE class_id = ?
              AND COALESCE(enrollment_status, 'active') = 'active'
            """,
            (class_id,),
        )
        roster_df = pd.DataFrame(roster_cursor, columns=['student_pk_id', '学号', '姓名'])

        if roster_df.empty:
            raise HTTPException(404, "此班级没有学生，无法导出。")

        # 2. 获取这个班级学生的此项作业成绩
        grades_cursor = conn.execute(
            """SELECT student_pk_id,
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
                      WHERE class_id = ?
                        AND COALESCE(enrollment_status, 'active') = 'active'
                  )""",
            (assignment_id, class_id)
        )
        grades_df = pd.DataFrame(grades_cursor, columns=['student_pk_id', '提交姓名', '分数', '状态', '评语'])

    final_df = roster_df.merge(grades_df, on='student_pk_id', how='left')

    export_filename = f"成绩_{class_info['name']}_{assignment['title']}.xlsx"
    # 确保作业目录存在
    assignment_dir = _build_assignment_storage_dir(assignment['course_id'], assignment['id'])
    assignment_dir.mkdir(parents=True, exist_ok=True)
    export_path = assignment_dir / export_filename

    final_df.to_excel(export_path, index=False)
    return FileResponse(export_path, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        filename=export_filename)


@router.get("/assignments/{assignment_id}/export-review-docx")
async def export_student_assignment_review_docx(
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
