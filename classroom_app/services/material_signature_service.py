"""Material adapters and durable, permission-scoped approval snapshots.

The artifact is built before a short transaction pins its content version.
Reviewers only receive the frozen artifact, never a writable course permission.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..database import get_db_connection
from . import signature_service
from .material_signature_revision_service import content_fingerprint, json_object, plan_content


DOCUMENT_POINTS = {
    "academic_grade_register": [("academic_final_material.grade_register.teacher_signature", "任课教师签字", "teacher_signature_ids", "")],
    "academic_exam_analysis": [
        ("academic_final_material.exam_analysis.department_review_signature", "系（教研室）审核", "department_signature_ids", "department_review_opinion"),
        ("academic_final_material.exam_analysis.dean_review_signature", "教学院长审核", "dean_signature_ids", "dean_review_opinion"),
    ],
    "assessment_plan": [
        ("assessment_plan.examiner_signature", "命题教师签名", "examiner_signature_ids", ""),
        ("assessment_plan.reviewer_signature", "系（教研室）主任审核", "reviewer_signature_ids", ""),
    ],
}
DOCUMENT_LABELS = {"academic_grade_register": "成绩登记表", "academic_exam_analysis": "试卷分析表", "assessment_plan": "考核计划表"}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def material_reference(value: dict) -> dict:
    kind = str(value.get("material_type") or "")
    record_id = str(value.get("material_id") or "").strip()
    if kind not in {"academic_final_material", "assessment_plan"} or not record_id or len(record_id) > 160:
        raise signature_service.SignatureServiceError(400, "请选择有效的材料记录。")
    if kind == "academic_final_material" and (not record_id.isdecimal() or int(record_id) <= 0):
        raise signature_service.SignatureServiceError(400, "材料记录编号无效。")
    return {"material_type": kind, "material_id": record_id}


def load_material(conn, user: dict, reference: dict, *, write: bool = True) -> dict:
    ref = material_reference(reference)
    actor = signature_service.build_signature_actor(conn, user)
    if actor["role"] != "teacher":
        raise signature_service.SignatureServiceError(403, "该材料操作仅面向教师账号。")
    table = "material_ai_import_records" if ref["material_type"] == "academic_final_material" else "assessment_plans"
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (ref["material_id"],)).fetchone()
    if not row:
        raise signature_service.SignatureServiceError(404, "材料不存在。")
    row = dict(row)
    owned = int(row.get("teacher_id") or 0) == actor["id"]
    allowed = owned or (not write and actor["is_super_admin"])
    if not allowed and not write and ref["material_type"] == "assessment_plan":
        from .assessment_plan_service import can_view_plan

        allowed = can_view_plan(row, actor["scope"])
    if not allowed:
        raise signature_service.SignatureServiceError(403, "无权操作这份材料。")
    if ref["material_type"] == "academic_final_material":
        if row.get("parse_status") != "completed":
            raise signature_service.SignatureServiceError(409, "材料尚未生成完成。")
        document_type = str(row.get("document_type") or "")
        payload = json_object(row.get("export_payload_json"))
        revision = row.get("signature_revision") or (f"source:{row['source_file_hash']}" if row.get("source_file_hash") else f"record:{ref['material_id']}")
    else:
        document_type = "assessment_plan"
        payload = plan_content(row)
        revision = row.get("signature_revision") or f"plan:{ref['material_id']}"
        if row.get("status") in {"generating", "importing", "parsing", "failed"}:
            raise signature_service.SignatureServiceError(409, "材料正在生成，请完成后操作。")
    if document_type not in DOCUMENT_POINTS:
        raise signature_service.SignatureServiceError(400, "该文档类型尚未接入材料签名。")
    fields = payload.get("fields") or {}
    title = " · ".join(str(v) for v in [DOCUMENT_LABELS[document_type], fields.get("course_name"), fields.get("class_name")] if v)
    return {**ref, "row": row, "payload": payload, "actor": actor, "document_type": document_type,
            "material_revision": str(revision), "title": title, "owner_id": int(row["teacher_id"]),
            "content_fingerprint": content_fingerprint(payload), "can_edit": owned or actor["is_super_admin"]}


def build_document_artifact(conn, material: dict, *, requested_format: str = "docx", allow_incomplete: bool = True):
    if material["document_type"] == "assessment_plan":
        from .assessment_plan_service import export_plan_artifact, get_assessment_plan

        return export_plan_artifact(conn, get_assessment_plan(conn, material["material_id"]),
                                    requested_format=requested_format, enforce_score_balance=not allow_incomplete)
    from .material_record_export_service import build_record_export_payload
    from .material_export_template_service import build_material_export_artifact
    from .academic_final_material_source_service import NativeAcademicSourceError

    try:
        payload = build_record_export_payload(material["row"], conn, for_export=True)
        return build_material_export_artifact(payload, fallback_filename=material["title"], requested_format=requested_format)
    except NativeAcademicSourceError as exc:
        raise signature_service.SignatureServiceError(409, str(exc)) from exc


def store_artifact(content: bytes, *, filename: str, media_type: str, area: str = "snapshots") -> dict:
    if area not in {"snapshots", "bundles"}:
        raise ValueError("invalid artifact area")
    digest = hashlib.sha256(content).hexdigest()
    suffix = Path(filename).suffix.lower()
    if suffix not in {".docx", ".xlsx", ".pdf", ".zip"}:
        raise ValueError("unsupported material artifact")
    directory = Path(DATA_DIR).resolve() / "material_workflows" / area / digest[:2]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}{suffix}"
    if not target.exists():
        descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"hash": digest, "suffix": suffix, "area": area, "filename": Path(filename).name,
            "media_type": media_type, "size": len(content)}


def artifact_path(artifact: dict) -> Path:
    digest = str(artifact.get("hash") or "")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise signature_service.SignatureServiceError(404, "文档快照标识无效。")
    suffix, area = artifact.get("suffix"), artifact.get("area", "snapshots")
    if suffix not in {".docx", ".xlsx", ".pdf", ".zip"} or area not in {"snapshots", "bundles"}:
        raise signature_service.SignatureServiceError(404, "文档快照标识无效。")
    path = Path(DATA_DIR).resolve() / "material_workflows" / area / digest[:2] / f"{digest}{suffix}"
    if not path.is_file():
        raise signature_service.SignatureServiceError(404, "文档快照文件不存在，请联系管理员恢复。")
    return path


def prepare_snapshot(user: dict, reference: dict) -> dict:
    # Read/build uses no write transaction. The caller subsequently pins this
    # exact revision while holding the material lock in its write transaction.
    with get_db_connection() as conn:
        material = load_material(conn, user, reference)
        try:
            artifact = build_document_artifact(conn, material)
        except ValueError as exc:
            raise signature_service.SignatureServiceError(409, str(exc)) from exc
    stored = store_artifact(artifact.content, filename=artifact.filename, media_type=artifact.media_type)
    identity = {key: material[key] for key in ("material_type", "material_id", "material_revision", "content_fingerprint", "owner_id")}
    snapshot_id = hashlib.sha256(dumps({**identity, "artifact_hash": stored["hash"]}).encode("utf-8")).hexdigest()
    return {**identity, "id": snapshot_id, "document_type": material["document_type"], "owner_role": "teacher",
            "title": material["title"], "payload_json": dumps(material["payload"]), "artifact_json": dumps(stored)}


def pin_snapshot(conn, user: dict, snapshot: dict) -> dict:
    table = "material_ai_import_records" if snapshot["material_type"] == "academic_final_material" else "assessment_plans"
    conn.execute(f"UPDATE {table} SET id = id WHERE id = ?", (snapshot["material_id"],))
    current = load_material(conn, user, snapshot)
    if any(current[key] != snapshot[key] for key in ("material_revision", "content_fingerprint", "owner_id")):
        raise signature_service.SignatureServiceError(409, "材料在准备申请时已更新，请重新查看后提交。")
    keys = ("id", "material_type", "material_id", "material_revision", "document_type", "owner_role", "owner_id", "title", "content_fingerprint", "payload_json", "artifact_json")
    conn.execute(f"INSERT INTO signature_material_snapshots ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)}) ON CONFLICT (id) DO NOTHING",
                 tuple(snapshot[key] for key in keys))
    return current


def check_request_snapshot(conn, request: dict) -> dict | None:
    snapshot_id = str(request.get("snapshot_id") or "")
    if not snapshot_id:
        return None  # Historical approvals retain their original contract.
    row = conn.execute("SELECT * FROM signature_material_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        raise signature_service.SignatureServiceError(409, "审批文档快照缺失，不能批准使用。")
    snapshot = dict(row)
    table = "material_ai_import_records" if snapshot["material_type"] == "academic_final_material" else "assessment_plans"
    conn.execute(f"UPDATE {table} SET id = id WHERE id = ?", (snapshot["material_id"],))
    current = load_material(conn, {"role": request["requester_role"], "id": request["requester_id"]}, snapshot)
    if any(current[key] != snapshot[key] for key in ("material_revision", "content_fingerprint", "owner_id")):
        raise signature_service.SignatureServiceError(409, "材料内容已变更，请申请人基于最新材料重新申请。")
    return snapshot


def authorized_request(conn, user: dict, request_id: int) -> dict:
    from .signature_workflow_service import get_request

    actor = signature_service.build_signature_actor(conn, user)
    request = get_request(conn, int(request_id))
    owner = (request["requester_role"], request["requester_id"]) == (actor["role"], actor["id"])
    reviewer = any((entry["role"], entry["id"]) == (actor["role"], actor["id"]) for entry in request["reviewers"])
    if not (owner or reviewer or actor["is_super_admin"]):
        raise signature_service.SignatureServiceError(404, "申请不存在或无权查看。")
    request["can_review"] = request["status"] == "pending" and (actor["is_super_admin"] or any(
        (entry["role"], entry["id"]) == (actor["role"], actor["id"]) and entry["status"] == "pending" for entry in request["reviewers"]))
    return request


def review_document_requests(conn, user: dict, request_id: int, *, action: str, note: str = "", expected_snapshot_id=None):
    """Review every eligible request for the file, independent of inbox paging."""
    from .signature_workflow_service import batch_review_access_requests

    request = authorized_request(conn, user, request_id)
    snapshot_id = request.get("snapshot_id")
    if not snapshot_id:
        raise signature_service.SignatureServiceError(409, "历史申请没有材料快照，请单独处理。")
    if expected_snapshot_id and expected_snapshot_id != snapshot_id:
        raise signature_service.SignatureServiceError(409, "审批文档已变化，请重新打开材料。")
    actor = signature_service.build_signature_actor(conn, user)
    rows = conn.execute("""SELECT request.id FROM signature_access_requests request
        WHERE request.snapshot_id = ? AND request.status = 'pending'
          AND (? = 1 OR EXISTS (SELECT 1 FROM signature_access_request_reviewers reviewer
              WHERE reviewer.request_id = request.id AND reviewer.reviewer_role = ?
                AND reviewer.reviewer_id = ? AND reviewer.status = 'pending'))
        ORDER BY request.id LIMIT 51""", (snapshot_id, int(actor["is_super_admin"]), actor["role"], actor["id"])).fetchall()
    return batch_review_access_requests(conn, user, [row["id"] for row in rows], action=action, note=note)
