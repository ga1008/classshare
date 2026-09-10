"""Teaching domain hooks for the authenticated C confirmation facade.

The caller owns the fresh-user receipt, session check and transaction. These
hooks are never executable through MCP or by a background tools delegation.
"""
from __future__ import annotations

import hashlib
import re

from fastapi import HTTPException

from . import offering_merge_service as merge
from .teaching_lifecycle_service import (
    build_teaching_delete_review, delete_unused_teaching_resource, validate_teaching_confirmation_inputs,
)


_REVISION = {"type": "str", "max_chars": 64}
ACTION_DEFINITIONS = {
    "delete_empty_class": {
        "label": "核对并删除空班级", "done_label": "已删除班级", "risk": "high",
        "execution_mode": "user_confirmation", "human_only": True, "roles": ["teacher"],
        "description": "仅准备本人确认提案。平台核对学生、课堂、合班、定向内容与教务引用，存在业务阻断时不能删除；用户手工输入班级名并确认后执行。",
        "fields": {"class_id": {"type": "int", "minimum": 1, "required": True}, "expected_review_hash": _REVISION},
    },
    "delete_unreferenced_course": {
        "label": "核对并删除未引用课程", "done_label": "已删除课程", "risk": "high",
        "execution_mode": "user_confirmation", "human_only": True, "roles": ["teacher"],
        "description": "仅准备本人确认提案。课堂、作业、文件、学习文档包与教学文档引用会阻断；本人核对课次模板及教务历史的处置后确认。不会删除共享材料文件。",
        "fields": {"course_id": {"type": "int", "minimum": 1, "required": True}, "expected_review_hash": _REVISION},
    },
    "merge_class_offerings": {
        "label": "核对并合并本人课堂", "done_label": "已合并课堂", "risk": "high",
        "execution_mode": "user_confirmation", "human_only": True, "roles": ["teacher"],
        "description": "仅准备本人确认提案。同教师、课程、学期且行政班不重叠；作业并存，保留学习和成绩历史，迁移后删除源课堂并留存人工恢复档案。运行中生成或未处理的读者范围会阻断。",
        "fields": {
            "target_offering_id": {"type": "int", "minimum": 1, "required": True},
            "source_offering_ids": {"type": "int_list", "required": True, "min_items": 1,
                "max_items": 11, "minimum": 1, "canonical_sorted": True},
            "expected_review_hash": _REVISION,
        },
    },
}


def _clean_params(action: str, params: dict) -> dict:
    if action not in ACTION_DEFINITIONS or not isinstance(params, dict):
        raise HTTPException(400, "教学确认操作无效。")
    fields = ACTION_DEFINITIONS[action]["fields"]
    if set(params) - set(fields):
        raise HTTPException(400, "教学确认包含未知参数。")
    clean = {}
    for key, field in fields.items():
        if key == "expected_review_hash":
            if key in params:
                value = params[key]
                if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                    raise HTTPException(400, "核对版本无效，请重新预览。")
                clean[key] = value
            continue
        value = params.get(key)
        if field["type"] == "int":
            if type(value) is not int or not 1 <= value <= 9223372036854775807:
                raise HTTPException(400, "教学资源编号必须为正整数。")
            clean[key] = value
        else:
            if (not isinstance(value, list) or not 1 <= len(value) <= 11
                    or any(type(item) is not int or not 1 <= item <= 9223372036854775807 for item in value)
                    or len(set(value)) != len(value)):
                raise HTTPException(400, "请选择 1 至 11 个不重复的源课堂编号。")
            clean[key] = sorted(value)
    return clean


def prepare_teaching_confirmation(conn, *, action: str, params: dict, user: dict) -> dict:
    if user.get("role") != "teacher":
        raise HTTPException(403, "当前身份不能执行教学管理确认。")
    clean = _clean_params(action, params)
    if action == "merge_class_offerings":
        try:
            preview = merge.build_merge_preview(conn, teacher_id=int(user["id"]),
                target_offering_id=clean["target_offering_id"], source_offering_ids=clean["source_offering_ids"])
        except merge.OfferingMergeError as exc:
            raise HTTPException(409, str(exc)) from exc
        warning_texts = ["源课堂迁入后将被删除；作业保持并存。档案用于人工恢复，没有一键撤销入口。", *preview["warnings"]]
        review = {"kind": "teaching_merge", "title": "核对并合并本人课堂",
            "summary": f"将 {len(clean['source_offering_ids'])} 个课堂并入 {preview['target']['course_name']} / {preview['target']['class_name']}",
            "review_hash": preview["review_hash"], "target": preview["target"], "sources": preview["sources"],
            "impact_sections": [{"key": item["table"], "label": item["label"], "effect": item["strategy"], "count": item["source_rows"]} for item in preview["tables"]],
            "warnings": [{"code": "merge_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], "message": text} for text in warning_texts],
            "can_execute": preview["can_execute"], "blockers": preview["blockers"],
            "expected_confirmation_text": preview["target"]["class_name"]}
    else:
        kind = "class" if action == "delete_empty_class" else "course"
        review = build_teaching_delete_review(conn, kind=kind, resource_id=clean[f"{kind}_id"], user=user)
    return {"params": {**clean, "expected_review_hash": review["review_hash"]}, "review": review}


def execute_teaching_confirmation(conn, *, action: str, params: dict, user: dict, confirmation_inputs: dict) -> dict:
    clean = _clean_params(action, params)
    if not clean.get("expected_review_hash"):
        raise HTTPException(400, "请先预览并核对本次操作。")
    prepared = prepare_teaching_confirmation(conn, action=action, params=clean, user=user)
    review = prepared["review"]
    if clean["expected_review_hash"] != review["review_hash"]:
        raise HTTPException(409, "教学数据已变化，本次确认失效，请重新预览。")
    if not review["can_execute"]:
        raise HTTPException(409, " ".join(review["blockers"]))
    validate_teaching_confirmation_inputs(confirmation_inputs, review)
    if action == "merge_class_offerings":
        try:
            result = merge.execute_offering_merge(conn, teacher_id=int(user["id"]),
                target_offering_id=clean["target_offering_id"], source_offering_ids=clean["source_offering_ids"],
                confirm_class_name=confirmation_inputs["confirmation_text"], expected_review_hash=clean["expected_review_hash"])
        except merge.OfferingMergeError as exc:
            raise HTTPException(409, str(exc)) from exc
        result = {**result, "label": "已合并课堂", "ref_id": result["target_offering_id"],
                  "url": f"/classroom/{result['target_offering_id']}"}
    else:
        kind = "class" if action == "delete_empty_class" else "course"
        result = delete_unused_teaching_resource(conn, kind=kind, resource_id=clean[f"{kind}_id"], user=user,
            expected_review_hash=clean["expected_review_hash"], confirmation_text=confirmation_inputs["confirmation_text"])
    return {**result, "confirmation_source": "authenticated_user"}
