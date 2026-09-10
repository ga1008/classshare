from .common import *
from ...services.ordinary_grade_record_service import (
    normalize_ordinary_grade_kind_override,
    ordinary_grade_assignment_kind_info,
)
from pydantic import ValidationError
from ...schemas.homework_contracts import AssessmentKindMutationRequest, AssessmentKindBatchConfirmationRequest
from ...services.assessment_classification_service import (
    ASSESSMENT_KIND_LABELS,
    assessment_kind_info,
    assessment_classification_impact,
    assessment_classification_impacts,
    enrich_assessment_classifications,
    normalize_assessment_kind,
    set_assignment_assessment_kind,
)
from ...services.assignment_creation_service import create_assignment_record


router = APIRouter()


@router.get("/classrooms/{class_offering_id}/assignments", response_class=JSONResponse)
def get_classroom_assignment_list(class_offering_id: int, limit: int = 30, offset: int = 0,
                                 user: dict = Depends(get_current_user)):
    from ...services.assignment_read_service import list_classroom_assignments
    with get_db_connection() as conn:
        return list_classroom_assignments(conn, class_offering_id=class_offering_id, user=user, limit=limit, offset=offset)


@router.get("/assignments/{assignment_id}/details", response_class=JSONResponse)
def get_assignment_details_json(assignment_id: str, user: dict = Depends(get_current_user)):
    from ...services.assignment_read_service import get_assignment_details
    with get_db_connection() as conn:
        return get_assignment_details(conn, assignment_id=assignment_id, user=user)


def _classification_request(model, payload):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(400, exc.errors(include_input=False, include_url=False)) from exc


def _get_classifiable_assignment(conn, assignment_id, teacher_id):
    row = conn.execute(
        """SELECT a.*, c.created_by_teacher_id, o.teacher_id AS offering_teacher_id,
                  lsea.id AS personal_stage_attempt_id
           FROM assignments a JOIN courses c ON c.id = a.course_id
           LEFT JOIN class_offerings o ON o.id = a.class_offering_id
           LEFT JOIN learning_stage_exam_attempts lsea ON lsea.assignment_id = a.id
           WHERE a.id = ? LIMIT 1""", (assignment_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "课堂任务不存在")
    item = dict(row)
    if not _teacher_can_access_assignment(conn, item, int(teacher_id)):
        raise HTTPException(403, "无权修改该课堂任务分类")
    if item.get("personal_stage_attempt_id"):
        _hide_personal_stage_asset()
    return item


@router.get("/classrooms/{class_offering_id}/assessment-classifications", response_class=JSONResponse)
async def list_assessment_classifications(class_offering_id: int, limit: int = 100, offset: int = 0,
                                         user: dict = Depends(get_current_teacher)):
    """Teacher confirmation inventory; no title-based classification writes."""
    with get_db_connection() as conn:
        if not conn.execute("SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ?",
                            (class_offering_id, int(user["id"]))).fetchone():
            raise HTTPException(404, "课堂不存在或无权访问")
        page_size = max(1, min(limit, 100))
        rows = conn.execute(
            """SELECT a.* FROM assignments a WHERE a.class_offering_id = ?
               AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts lsea WHERE lsea.assignment_id = a.id)
               ORDER BY a.id LIMIT ? OFFSET ?""",
            (class_offering_id, page_size + 1, max(0, offset)),
        ).fetchall()
        impacts = assessment_classification_impacts(conn, rows[:page_size], teacher_id=int(user["id"]))
        items = []
        for row in rows[:page_size]:
            item = dict(row)
            # Suggestions are review-only; broad 'exam/quiz' never guesses a period.
            title = str(item.get("title") or "")
            suggestion = None
            if "期中" in title and "期末" not in title:
                suggestion = "midterm"
            elif "期末" in title and "期中" not in title:
                suggestion = "final"
            elif not any(word in title for word in ("期中", "期末", "考试", "测验", "测评", "随堂测")) and any(word in title for word in ("作业", "实验", "练习")):
                suggestion = "homework"
            items.append({"assignment_id": item["id"], "title": title, "status": item.get("status"),
                          "class_offering_id": class_offering_id, **assessment_kind_info(item),
                          "ordinary_grade_kind_override": item.get("ordinary_grade_kind_override"),
                          "impact": impacts[str(item["id"])],
                          "suggested_assessment_kind": suggestion})
    return {"status": "success", "assignments": items, "next_offset": max(0, offset) + page_size if len(rows) > page_size else None,
            "assessment_kind_options": [{"value": key, "label": label} for key, label in ASSESSMENT_KIND_LABELS.items()]}


@router.get("/assignments/{assignment_id}/assessment-kind", response_class=JSONResponse)
async def get_assignment_assessment_kind(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        assignment = _get_classifiable_assignment(conn, assignment_id, int(user["id"]))
        impact = assessment_classification_impact(conn, assignment, teacher_id=int(user["id"]))
        revisions = [dict(row) for row in conn.execute(
            "SELECT * FROM assignment_classification_revisions WHERE assignment_id = ? ORDER BY version DESC LIMIT 50",
            (str(assignment_id),),
        ).fetchall()]
    return {"status": "success", "assignment_id": assignment_id, "title": assignment.get("title") or "",
            **assessment_kind_info(assignment), "impact": impact, "revisions": revisions}


@router.patch("/assignments/{assignment_id}/assessment-kind", response_class=JSONResponse)
async def update_assignment_assessment_kind(assignment_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    data = _classification_request(AssessmentKindMutationRequest, await request.json())
    with get_db_connection() as conn:
        assignment = _get_classifiable_assignment(conn, assignment_id, int(user["id"]))
        impact = assessment_classification_impact(conn, assignment, teacher_id=int(user["id"]))
        result = set_assignment_assessment_kind(conn, assignment, assessment_kind=data.assessment_kind,
                                               expected_version=data.expected_version, teacher_id=int(user["id"]), reason=data.reason)
        conn.commit()
    return {"status": "success", **result, "impact": impact}


@router.post("/assignments/assessment-kinds/confirm", response_class=JSONResponse)
async def confirm_assignment_assessment_kinds(request: Request, user: dict = Depends(get_current_teacher)):
    data = _classification_request(AssessmentKindBatchConfirmationRequest, await request.json())
    ids = [str(item.assignment_id) for item in data.items]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "批量确认不能包含重复任务")
    with get_db_connection() as conn:
        # Authorize every target before writing; use deterministic lock order.
        assignments = {key: _get_classifiable_assignment(conn, key, int(user["id"])) for key in sorted(ids)}
        changes = {str(item.assignment_id): item for item in data.items}
        conn.execute("SAVEPOINT assessment_classification_batch")
        try:
            results = []
            for key in sorted(ids):
                change = changes[key]
                results.append(set_assignment_assessment_kind(
                    conn, assignments[key], assessment_kind=change.assessment_kind,
                    expected_version=change.expected_version, teacher_id=int(user["id"]),
                    source="teacher_confirm", reason=change.reason or data.reason,
                ))
            conn.execute("RELEASE SAVEPOINT assessment_classification_batch")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT assessment_classification_batch")
            conn.execute("RELEASE SAVEPOINT assessment_classification_batch")
            raise
        conn.commit()
    return {"status": "success", "assignments": results, "changed_count": sum(item["changed"] for item in results)}


@router.patch("/assignments/{assignment_id}/ordinary-grade-kind", response_class=JSONResponse)
async def update_assignment_ordinary_grade_kind(
    assignment_id: str,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """Override only how a classroom task is counted in the ordinary-grade record."""
    data = await request.json()
    try:
        override = normalize_ordinary_grade_kind_override(data.get("kind"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        assignment = conn.execute(
            """
            SELECT a.*,
                   o.teacher_id AS offering_teacher_id
            FROM assignments a
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            WHERE a.id = ?
            """,
            (assignment_id,),
        ).fetchone()
        if not assignment:
            raise HTTPException(404, "课堂任务不存在")
        assignment_dict = dict(assignment)
        if not assignment_dict.get("class_offering_id"):
            raise HTTPException(400, "只有已经发布到课堂的任务才能设置平时成绩用途")
        if not _teacher_can_access_assignment(conn, assignment_dict, int(user["id"])):
            raise HTTPException(403, "无权修改该课堂任务的平时成绩用途")
        if is_personal_stage_exam_assignment(conn, assignment_id):
            _hide_personal_stage_asset()
        if assignment_dict.get("assessment_kind"):
            raise HTTPException(409, "该任务已确认三分类，请通过任务分类修改；旧平时成绩用途仅支持历史任务")

        updated_at = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE assignments
            SET ordinary_grade_kind_override = ?,
                ordinary_grade_kind_updated_at = ?,
                ordinary_grade_kind_updated_by_teacher_id = ?
            WHERE id = ?
            """,
            (override or None, updated_at, int(user["id"]), assignment_id),
        )
        assignment_dict.update(
            {
                "ordinary_grade_kind_override": override,
                "ordinary_grade_kind_updated_at": updated_at,
                "ordinary_grade_kind_updated_by_teacher_id": int(user["id"]),
            }
        )
        kind_info = ordinary_grade_assignment_kind_info(assignment_dict)
        # The deprecated endpoint keeps its historical response contract. New
        # material eligibility is resolved independently from assessment_kind.
        legacy_kind = override or kind_info["ordinary_grade_auto_kind"]
        kind_info.update(
            kind=legacy_kind,
            ordinary_grade_kind=legacy_kind,
            ordinary_grade_kind_source="manual" if override else "auto",
            legacy_only=True,
        )
        conn.commit()

    return {
        "status": "success",
        "assignment_id": assignment_id,
        "class_offering_id": int(assignment_dict["class_offering_id"]),
        "title": assignment_dict.get("title") or "",
        **kind_info,
        "message": (
            f"已将“{assignment_dict.get('title') or '课堂任务'}”在平时成绩表中设为"
            f"{'平时作业' if kind_info['kind'] == 'assignment' else '测验'}。"
            + ("学生答题与试卷批改方式保持不变。" if assignment_dict.get("exam_paper_id") else "")
            + "新建正式成绩材料仍需确认任务分类。"
        ),
    }


@router.post(
    "/courses/{course_id}/assignments",
    response_class=JSONResponse,
    response_model=AssignmentMutationResponse,
    response_model_exclude_unset=True,
)
async def create_assignment(course_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    """V4.0: 在指定课程下创建新作业"""
    data = await request.json()
    class_offering_id = data.get('class_offering_id')
    allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data))
    learning_stage_key = _get_learning_stage_key(data, class_offering_id=class_offering_id)
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        created = create_assignment_record(conn, teacher_id=int(user["id"]), course_id=course_id, data=data,
                                           allowed_file_types_json=allowed_file_types_json, learning_stage_key=learning_stage_key)
        new_id, actual_course_id = created["id"], created["course_id"]
        schedule_fields, classification = created["schedule_fields"], created["classification"]
        if schedule_fields["status"] == "published":
            try:
                create_assignment_published_notifications(
                    conn,
                    new_id,
                    send_email_notification=_wants_assignment_email_notification(data),
                )
            except Exception as exc:
                print(f"[MESSAGE_CENTER] assignment publish notify failed: {exc}")
        sync_assignment_due_reminders(
            conn,
            new_id,
            status=schedule_fields["status"],
            due_at=schedule_fields["due_at"],
            class_offering_id=class_offering_id,
            title=str(data.get('title') or ''),
        )
        conn.commit()
    # 作业文件夹现在按 Course / Assignment 组织
    assignment_dir = _build_assignment_storage_dir(actual_course_id, new_id)
    assignment_dir.mkdir(parents=True, exist_ok=True)
    return {
        "status": "success",
        "new_assignment_id": new_id,
        **{key: value for key, value in classification.items() if key not in {"assignment_id", "changed"}},
        "assignment_status": schedule_fields["status"],
        "due_at": schedule_fields["due_at"],
    }


@router.put(
    "/assignments/{assignment_id}",
    response_class=JSONResponse,
    response_model=AssignmentMutationResponse,
    response_model_exclude_unset=True,
)
async def update_assignment(assignment_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    from ...services.assignment_management_service import update_assignment_record

    data = await request.json()
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        result = update_assignment_record(conn, assignment_id=assignment_id, teacher_id=int(user["id"]), data=data)
        conn.commit()
    return result


@router.delete(
    "/assignments/{assignment_id}",
    response_class=JSONResponse,
    response_model=AssignmentMutationResponse,
    response_model_exclude_unset=True,
)
async def delete_assignment(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        assignment = conn.execute(
            """SELECT a.id,
                      a.course_id,
                      a.class_offering_id,
                      c.created_by_teacher_id,
                      o.teacher_id AS offering_teacher_id
               FROM assignments a
               JOIN courses c ON a.course_id = c.id
               LEFT JOIN class_offerings o ON o.id = a.class_offering_id
               WHERE a.id = ?""",
            (assignment_id,)
        ).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        if not _teacher_can_access_assignment(conn, dict(assignment), int(user["id"])):
            raise HTTPException(403, "无权删除该作业")
        if is_personal_stage_exam_assignment(conn, assignment_id):
            _hide_personal_stage_asset()

        conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        cancel_assignment_due_reminders(conn, assignment_id)
        conn.commit()
    delete_storage_tree(_build_assignment_storage_dir(assignment['course_id'], assignment_id))
    return {"status": "success", "deleted_assignment_id": assignment_id}


@router.get(
    "/assignments/time-state",
    response_class=JSONResponse,
    response_model=AssignmentTimeStateResponse,
    response_model_exclude_unset=True,
)
async def get_assignment_time_state(request: Request, user: dict = Depends(get_current_user)):
    raw_ids = request.query_params.get("ids") or request.query_params.get("assignment_ids") or ""
    assignment_ids = []
    for part in str(raw_ids).split(","):
        text = part.strip()
        if not text:
            continue
        try:
            assignment_ids.append(int(text))
        except ValueError as exc:
            raise HTTPException(400, "作业 ID 格式无效") from exc
    assignment_ids = list(dict.fromkeys(assignment_ids))[:50]
    now_dt = utc_like_now()
    if not assignment_ids:
        return {"status": "success", "server_now": now_dt.isoformat(), "assignments": []}

    with get_db_connection() as conn:
        close_overdue_assignments(conn, now_dt=now_dt)
        placeholders = ",".join("?" for _ in assignment_ids)
        rows = conn.execute(
            f"""
            SELECT a.*,
                   c.created_by_teacher_id,
                   o.teacher_id AS offering_teacher_id
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            WHERE a.id IN ({placeholders})
            """,
            tuple(assignment_ids),
        ).fetchall()
        assignments = []
        for row in rows:
            item = dict(row)
            if user.get("role") == "teacher":
                if not _teacher_can_access_assignment(conn, item, int(user["id"])):
                    continue
            elif user.get("role") == "student":
                if str(item.get("status") or "").strip().lower() == "new":
                    continue
                if not student_can_access_assignment(conn, str(item["id"]), int(user["id"])):
                    continue
            else:
                continue
            item = enrich_assignment_runtime_view(item, now_dt=now_dt)
            assignments.append(serialize_assignment_time_state(item, now_dt=now_dt))
        conn.commit()

    return {"status": "success", "server_now": now_dt.isoformat(), "assignments": assignments}


@router.get(
    "/courses/{course_id}/assignment-stats",
    response_class=JSONResponse,
    response_model=CourseAssignmentStatsResponse,
    response_model_exclude_unset=True,
)
async def get_course_assignment_stats(course_id: int, user: dict = Depends(get_current_teacher),
                                      assessment_kind: str | None = None, class_offering_id: int | None = None,
                                      semester_id: int | None = None):
    return await asyncio.to_thread(_read_course_assignment_stats, course_id, user, assessment_kind, class_offering_id, semester_id)


def _read_course_assignment_stats(course_id, user, assessment_kind=None, class_offering_id=None, semester_id=None):
    """课程维度统计：汇总某课程下所有作业的提交率、批改进度和平均分。"""
    if assessment_kind is not None:
        try:
            assessment_kind = normalize_assessment_kind(assessment_kind)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    from ...services.score_projection_service import load_submission_score_facts
    filters, params = ["a.course_id = ?"], [course_id]
    for column, value in (("a.assessment_kind", assessment_kind), ("a.class_offering_id", class_offering_id), ("o.semester_id", semester_id)):
        if value is not None:
            filters.append(f"{column} = ?")
            params.append(value)

    def summarize(facts):
        scores = [fact["effective_score"] for fact in facts if fact["has_effective_score"]]
        return {
            "total": len(facts), "graded": len(scores), "effective_score_count": len(scores),
            "submitted": sum(f["status"] == "submitted" and not f.get("resubmission_allowed") and not f.get("is_absence_score") for f in facts),
            "grading": sum(f["status"] == "grading" for f in facts),
            "absence": sum(bool(f.get("is_absence_score")) for f in facts),
            "grading_review": sum(f["status"] == "grading_review" and not f["has_effective_score"] for f in facts),
            "regrading": sum(f["is_regrading"] for f in facts),
            "returned": sum(bool(f.get("resubmission_allowed")) for f in facts),
            "late": sum(bool(f.get("is_late_submission")) for f in facts),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "max_score": max(scores, default=None), "min_score": min(scores, default=None),
            "pass_rate": round(sum(s >= 60 for s in scores) / len(scores) * 100, 1) if scores else None,
        }

    with get_db_connection() as conn:
        owned = conn.execute(
            "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
            (course_id, user["id"]),
        ).fetchone()
        if not owned:
            raise HTTPException(404, "课程不存在或无权访问")

        assignments = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT a.id, a.title, a.status, a.grading_mode, a.class_offering_id,
                       a.due_at, a.availability_mode, a.exam_paper_id,
                       a.assessment_kind, a.assessment_kind_version, a.assessment_kind_source,
                       o.semester_id, o.semester AS semester_name
                FROM assignments a
                LEFT JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE {' AND '.join(filters)}
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_stage_exam_attempts lsea
                      WHERE lsea.assignment_id = a.id
                  )
                ORDER BY a.created_at DESC
                """,
                tuple(params),
            )
        ]

        by_assignment = {}
        for fact in load_submission_score_facts(conn, assignment_ids=[a["id"] for a in assignments], student_view=False, include_content=False):
            by_assignment.setdefault(str(fact["assignment_id"]), []).append(fact)
        stats_list, grouped = [], {}
        for a in assignments:
            facts = by_assignment.get(str(a["id"]), [])
            stats_list.append({
                "assignment_id": a["id"],
                "title": a["title"],
                "status": a.get("effective_status") or a["status"],
                "class_offering_id": a["class_offering_id"], "semester_id": a["semester_id"], "semester_name": a["semester_name"],
                **assessment_kind_info(a), **summarize(facts),
            })
            group = grouped.setdefault((a["class_offering_id"], a["semester_id"], a["assessment_kind"]), {
                "class_offering_id": a["class_offering_id"], "semester_id": a["semester_id"], "semester_name": a["semester_name"],
                "assessment_kind": a["assessment_kind"], "assessment_kind_label": assessment_kind_info(a)["assessment_kind_label"],
                "task_count": 0, "facts": [],
            })
            group["task_count"] += 1
            group["facts"].extend(facts)
        categories = []
        for group in grouped.values():
            summary = summarize(group.pop("facts"))
            categories.append({**group, **summary})

    return {"status": "success", "course_id": course_id, "assignments": stats_list, "categories": categories,
            "score_scale": 100, "score_summary_label": "已评分任务均分",
            "counting_note": "total为提交记录数，graded为有效分记录数；重批、迟交、退回等标记可交叠。均分及及格率只以有效分记录为分母。"}
