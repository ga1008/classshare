from .common import *
from ...services.submission_write_guard import submission_write_version


router = APIRouter()


@router.get(
    "/assignments/{assignment_id}/draft",
    response_class=JSONResponse,
    response_model=AssignmentDraftResponse,
    response_model_exclude_unset=True,
)
def get_assignment_draft(assignment_id: str, user: dict = Depends(get_current_student)):
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)
        draft_submission = _ensure_student_can_save_assignment_draft(
            conn,
            assignment=assignment,
            student_id=int(user["id"]),
        )
        draft = _load_submission_draft(conn, assignment_id, int(user["id"]))
        return {**_serialize_submission_draft(conn, draft, assignment_id),
                "submission_version": submission_write_version(draft_submission)}


@router.post(
    "/assignments/{assignment_id}/draft",
    response_class=JSONResponse,
    response_model=AssignmentDraftSaveResponse,
    response_model_exclude_unset=True,
)
async def save_assignment_draft(
    assignment_id: str,
    answers_json: str = Form(""),
    current_page: int = Form(0),
    client_updated_at: str = Form(""),
    replace_question_ids: str = Form("[]"),
    expected_submission_version: str = Form(""),
    manifest: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(get_current_student),
):
    # FastAPI supplies a string; direct internal callers may omit the Form field.
    expected_version = expected_submission_version if isinstance(expected_submission_version, str) else ""
    upload_files = [file for file in (files or []) if file and str(file.filename or "").strip()]
    if not upload_files:
        return await asyncio.to_thread(
            _save_assignment_draft_without_files_sync,
            assignment_id=assignment_id,
            student_pk_id=int(user["id"]),
            answers_json=answers_json,
            current_page=current_page,
            client_updated_at=client_updated_at,
            replace_question_ids=replace_question_ids,
            expected_submission_version=expected_version,
        )

    staging_dir: Path | None = None
    move_backup_dir: Path | None = None
    moved_draft_files: list[tuple[Path, Path | None]] = []
    old_file_paths: list[str] = []
    retired_paths: list[tuple[Path, Path]] = []
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)
        draft_submission = _ensure_student_can_save_assignment_draft(
            conn,
            assignment=assignment,
            student_id=int(user["id"]),
        )

        replace_ids = {
            str(item or "").strip()
            for item in _parse_json_list(replace_question_ids or "[]", field_name="replace_question_ids")
            if str(item or "").strip()
        }
        manifest_items = _parse_json_list(manifest or "[]", field_name="manifest") if manifest else []
        manifest_by_path: dict[str, dict[str, Any]] = {}
        for item in manifest_items:
            if not isinstance(item, dict):
                continue
            try:
                relative_path = normalize_submission_relative_path(
                    str(item.get("relative_path") or ""),
                    fallback_name=str(item.get("file_name") or item.get("filename") or "upload.bin"),
                )
            except HTTPException:
                continue
            manifest_by_path[relative_path.lower()] = item

        prepared_entries = _validate_upload_entries(upload_files, manifest)
        allowed_file_types = decode_allowed_file_types_json(assignment.get("allowed_file_types_json"))
        attachment_policies = _load_exam_attachment_policies(conn, assignment)
        draft_dir = _build_submission_draft_storage_dir(assignment["course_id"], assignment["id"], int(user["id"]))
        staging_dir = draft_dir.with_name(f"{draft_dir.name}.__staging__{uuid.uuid4().hex}")
        try:
            storage_result = await store_submission_files(
                staging_dir,
                prepared_entries,
                allowed_file_types,
                is_allowed_file=lambda entry: _is_allowed_assignment_submission_file(
                    entry.relative_path,
                    entry.content_type,
                    allowed_file_types,
                    attachment_policies,
                ),
            )
            storage_result.dropped_files = _enrich_dropped_file_details(
                storage_result.dropped_files,
                allowed_file_types,
                attachment_policies,
            )
            if storage_result.dropped_files:
                raise HTTPException(
                    status_code=400,
                    detail=_dropped_files_error_detail(
                        storage_result.dropped_files,
                        action_label="保存到服务器草稿",
                    ),
                )

        except Exception:
            if staging_dir:
                delete_storage_tree(staging_dir)
            raise

        try:
            begin_immediate_transaction(conn)
            lock_submission_writer(conn, assignment_id, int(user["id"]))
            current_submission = _ensure_student_can_save_assignment_draft(conn, assignment=assignment, student_id=int(user["id"]))
            verify_submission_write(current_submission, draft_submission, actor_role="student",
                                    client_version=expected_version)
            if storage_result.stored_files:
                active_draft = _load_submission_draft(conn, assignment_id, int(user["id"]))
                remaining_rows = _load_submission_draft_files(conn, int(active_draft["id"])) if active_draft else []
                remaining_count = 0
                remaining_size = 0
                for row in remaining_rows:
                    if str(row["question_id"] or "") in replace_ids:
                        continue
                    remaining_count += 1
                    remaining_size += int(row["file_size"] or 0)
                next_count = remaining_count + len(storage_result.stored_files)
                next_size = remaining_size + sum(int(file_info.file_size or 0) for file_info in storage_result.stored_files)
                if next_count > MAX_SUBMISSION_FILE_COUNT:
                    raise HTTPException(413, f"草稿附件数量不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")
                if next_size > MAX_SUBMISSION_TOTAL_BYTES:
                    raise HTTPException(
                        413,
                        f"草稿附件总大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB"
                        f"（当前 {next_size / 1024 / 1024:.1f}MB）",
                    )
            if storage_result.stored_files:
                # Per-assignment screenshot hash library: refuse images that
                # another student already holds, or that this student already
                # placed under a different question of the same draft.
                _raise_if_duplicate_images(
                    conn,
                    assignment=assignment,
                    student_pk_id=int(user["id"]),
                    stored_files=storage_result.stored_files,
                    question_ids={
                        path_key: str(item.get("question_id") or "").strip()
                        for path_key, item in manifest_by_path.items()
                    },
                    options=HashCheckOptions(
                        include_own_draft=True,
                        replaced_question_ids=set(replace_ids),
                        replaced_relative_paths={
                            file_info.relative_path for file_info in storage_result.stored_files
                        },
                    ),
                    action_label="保存到服务器草稿",
                )
                move_backup_dir = draft_dir.with_name(f"{draft_dir.name}.__replace_backup__{uuid.uuid4().hex}")
                moved_draft_files = _move_stored_files_to_final_dir(
                    storage_result.stored_files,
                    staging_dir=staging_dir,
                    final_dir=draft_dir,
                    backup_dir=move_backup_dir,
                )
            draft = _ensure_submission_draft(
                conn,
                assignment_id=assignment_id,
                student_pk_id=int(user["id"]),
                answers_json=answers_json,
                current_page=current_page,
                client_updated_at=client_updated_at,
            )
            old_file_paths = list(draft.get("retired_file_paths") or []) + _delete_draft_file_rows_for_questions(
                conn,
                draft_id=int(draft["id"]),
                question_ids=replace_ids,
            )
            for file_info in storage_result.stored_files:
                manifest_item = manifest_by_path.get(file_info.relative_path.lower(), {})
                question_id = str(manifest_item.get("question_id") or "").strip()
                kind = str(manifest_item.get("kind") or "file").strip() or "file"
                duplicate_rows = conn.execute(
                    """
                    SELECT stored_path
                    FROM submission_draft_files
                    WHERE draft_id = ? AND LOWER(relative_path) = LOWER(?)
                    """,
                    (int(draft["id"]), file_info.relative_path),
                ).fetchall()
                old_file_paths.extend(
                    str(row["stored_path"] or "")
                    for row in duplicate_rows
                    if str(row["stored_path"] or "").strip()
                )
                conn.execute(
                    """
                    DELETE FROM submission_draft_files
                    WHERE draft_id = ? AND LOWER(relative_path) = LOWER(?)
                    """,
                    (int(draft["id"]), file_info.relative_path),
                )
                conn.execute(
                    """
                    INSERT INTO submission_draft_files (
                        draft_id, question_id, kind, original_filename, relative_path,
                        stored_path, mime_type, file_size, file_ext, file_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(draft["id"]),
                        question_id,
                        kind,
                        file_info.original_filename,
                        file_info.relative_path,
                        file_info.stored_path,
                        file_info.mime_type,
                        file_info.file_size,
                        file_info.file_ext,
                        file_info.file_hash,
                        datetime.now().isoformat(),
                    ),
            )
            new_file_paths = {str(file_info.stored_path) for file_info in storage_result.stored_files}
            retired_paths = _quarantine_submission_paths(old_file_paths, keep_paths=new_file_paths)
            conn.commit()
        except Exception:
            _restore_quarantined_submission_paths(retired_paths)
            _restore_moved_draft_files(moved_draft_files)
            conn.rollback()
            raise
        finally:
            if staging_dir:
                delete_storage_tree(staging_dir)
            if move_backup_dir:
                delete_storage_tree(move_backup_dir)

        _discard_quarantined_submission_paths(retired_paths)

        draft = _load_submission_draft(conn, assignment_id, int(user["id"]))
        payload = _serialize_submission_draft(conn, draft, assignment_id)
        payload.update(
            {
                "status": "success",
                "stored_file_count": len(storage_result.stored_files),
                **_dropped_files_response_fields(
                    storage_result.dropped_files,
                    action_label="保存到服务器草稿",
                ),
            }
        )
        return payload


@router.get("/assignments/{assignment_id}/draft-files/{file_id}")
async def download_assignment_draft_file(
    assignment_id: str,
    file_id: int,
    user: dict = Depends(get_current_student),
):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT sdf.*, sd.assignment_id, sd.student_pk_id
            FROM submission_draft_files sdf
            JOIN submission_drafts sd ON sd.id = sdf.draft_id
            WHERE sdf.id = ? AND sd.assignment_id = ? AND sd.student_pk_id = ?
            LIMIT 1
            """,
            (int(file_id), assignment_id, int(user["id"])),
        ).fetchone()
        if not row:
            raise HTTPException(404, "草稿附件不存在")
        file_dict = dict(row)
    physical_path = resolve_submission_file_path(str(file_dict.get("stored_path") or "")) or Path(str(file_dict.get("stored_path") or ""))
    physical_path = Path(physical_path)
    if not physical_path.exists() or not physical_path.is_file():
        raise HTTPException(404, "草稿附件文件不存在")
    return FileResponse(
        physical_path,
        media_type=file_dict.get("mime_type") or "application/octet-stream",
        filename=file_dict.get("original_filename") or physical_path.name,
    )


@router.post("/assignments/{assignment_id}/attachment-hash-check", response_class=JSONResponse)
def check_assignment_attachment_hashes(
    assignment_id: str,
    payload: dict = Body(default={}),
    user: dict = Depends(get_current_student),
):
    """Client pre-check against the per-assignment screenshot hash library.

    The browser hashes each picked image (SHA-256) before uploading so the
    student gets an immediate, specific reason instead of a failed upload.
    The draft / submit routes re-check server-side, so this is advisory only.
    """
    items = parse_hash_check_items((payload or {}).get("items"))
    replaced_question_ids = {
        str(value or "").strip()
        for value in ((payload or {}).get("replace_question_ids") or [])
        if str(value or "").strip()
    }
    with get_db_connection() as conn:
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = dict(assignment)
        if not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该破境试炼只对指定学生开放")
        if not items:
            return {"status": "success", "results": [], "conflicts": []}
        conflicts = find_image_hash_conflicts(
            conn,
            assignment_id=assignment_id,
            student_pk_id=int(user["id"]),
            candidates=items,
            question_labels=load_assignment_question_labels(conn, assignment),
            options=HashCheckOptions(include_own_draft=True, replaced_question_ids=replaced_question_ids),
        )
    conflict_by_hash = {conflict.file_hash: conflict.to_dict() for conflict in conflicts}
    results = []
    for item in items:
        conflict = conflict_by_hash.get(item.file_hash)
        results.append(
            {
                "hash": item.file_hash,
                "question_id": item.question_id,
                "file_name": item.file_name,
                "status": f"duplicate_{conflict['kind']}" if conflict else "ok",
                "conflict": conflict,
                "message": conflict["message"] if conflict else "",
            }
        )
    return {"status": "success", "results": results, "conflicts": list(conflict_by_hash.values())}


@router.delete("/assignments/{assignment_id}/draft-files/{file_id}", response_class=JSONResponse)
def delete_assignment_draft_file(
    assignment_id: str,
    file_id: int,
    expected_submission_version: str = "",
    user: dict = Depends(get_current_student),
):
    """Remove a single server-draft attachment (frees its hash claim)."""
    retired_paths: list[tuple[Path, Path]] = []
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)
        draft_submission = _ensure_student_can_save_assignment_draft(
            conn,
            assignment=assignment,
            student_id=int(user["id"]),
        )
        try:
            begin_immediate_transaction(conn)
            lock_submission_writer(conn, assignment_id, int(user["id"]))
            current_submission = _ensure_student_can_save_assignment_draft(
                conn, assignment=assignment, student_id=int(user["id"]),
            )
            verify_submission_write(current_submission, draft_submission, actor_role="student",
                                    client_version=expected_submission_version or "")
            row = conn.execute(
                """
                SELECT sdf.id, sdf.draft_id, sdf.stored_path
                FROM submission_draft_files sdf
                JOIN submission_drafts sd ON sd.id = sdf.draft_id
                WHERE sdf.id = ? AND sd.assignment_id = ? AND sd.student_pk_id = ?
                LIMIT 1
                """,
                (int(file_id), assignment_id, int(user["id"])),
            ).fetchone()
            if not row:
                raise HTTPException(404, "草稿附件不存在")
            row = dict(row)
            conn.execute("DELETE FROM submission_draft_files WHERE id = ?", (int(row["id"]),))
            conn.execute(
                """
                UPDATE submission_drafts
                SET server_updated_at = ?, server_version = COALESCE(server_version, 0) + 1
                WHERE id = ?
                """,
                (datetime.now().isoformat(), int(row["draft_id"])),
            )
            stored_path = str(row.get("stored_path") or "")
            if stored_path:
                retired_paths = _quarantine_submission_paths([stored_path])
            conn.execute("SELECT 1")
            conn.commit()
        except Exception:
            _restore_quarantined_submission_paths(retired_paths)
            conn.rollback()
            raise
        _discard_quarantined_submission_paths(retired_paths)
        draft = _load_submission_draft(conn, assignment_id, int(user["id"]))
        payload = _serialize_submission_draft(conn, draft, assignment_id)
    payload.update({"status": "success", "deleted_file_id": int(file_id)})
    return payload


@router.get("/assignments/{assignment_id}/draft-files/{file_id}/image")
async def get_assignment_draft_file_image(
    assignment_id: str,
    file_id: int,
    variant: str = "thumb",
    user: dict = Depends(get_current_student),
):
    """Compressed thumbnail / preview of a server-draft image (falls back to the original)."""
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT sdf.*
            FROM submission_draft_files sdf
            JOIN submission_drafts sd ON sd.id = sdf.draft_id
            WHERE sdf.id = ? AND sd.assignment_id = ? AND sd.student_pk_id = ?
            LIMIT 1
            """,
            (int(file_id), assignment_id, int(user["id"])),
        ).fetchone()
        if not row:
            raise HTTPException(404, "草稿附件不存在")
        file_dict = dict(row)
    is_image = str(file_dict.get("mime_type") or "").startswith("image/") or is_hash_guarded_attachment(
        str(file_dict.get("relative_path") or ""), file_dict.get("mime_type"), file_dict.get("kind"),
    )
    if not is_image:
        raise HTTPException(400, "Only image files support image variants")
    physical_path = resolve_submission_file_path(str(file_dict.get("stored_path") or "")) or str(file_dict.get("stored_path") or "")
    physical_path = Path(physical_path)
    if not physical_path.exists() or not physical_path.is_file():
        raise HTTPException(404, "草稿附件文件不存在")
    variant_path = await resolve_submission_image_variant(
        physical_path, str(file_dict.get("file_hash") or ""), normalize_image_variant(variant)
    )
    cache_headers = {"Cache-Control": "private, max-age=604800"}
    if variant_path is None:
        return FileResponse(physical_path, media_type=file_dict.get("mime_type") or "application/octet-stream", headers=cache_headers)
    return FileResponse(variant_path, media_type=IMAGE_VARIANT_MIME_TYPE, headers=cache_headers)
