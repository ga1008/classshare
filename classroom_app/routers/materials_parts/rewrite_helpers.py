from .common import *
from .generation_helpers import *
from .final_material_helpers import *


async def _run_ai_material_rewrite(
    *,
    material_id: int,
    mode: str,
    prompt: str,
    user: dict,
) -> dict[str, Any]:
    normalized_mode = "regenerate" if str(mode or "").strip().lower() == "regenerate" else "optimize"
    with get_db_connection() as conn:
        material = dict(ensure_teacher_material_owner(conn, material_id, user["id"]))
        context_rows = _collect_material_context_rows(conn, material)
        conn.execute(
            "UPDATE course_materials SET ai_optimize_status = 'running', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), material_id),
        )
        conn.commit()

    try:
        attachment = await _build_material_context_attachment(material, context_rows)
        raw_result = await _call_ai_chat(
            _build_ai_material_rewrite_system_prompt(normalized_mode),
            _build_ai_material_rewrite_user_prompt(
                mode=normalized_mode,
                material=material,
                prompt=prompt,
                attachment=attachment,
            ),
            capability="thinking",
            response_format="json",
            file_texts=[{"name": attachment.get("title") or material["name"], "content": attachment.get("content") or ""}],
            task_type="material_ai_regenerate" if normalized_mode == "regenerate" else "material_ai_optimize",
            task_label=f"materials:ai-{normalized_mode}",
            timeout=300.0,
        )
        fallback_title = str(material.get("name") or "课程材料")
        parse_result = _build_generic_material_parse_result(
            raw_result=raw_result,
            fallback_title=fallback_title,
            attachments=[attachment],
            ai_used=True,
        )
        markdown_content = build_import_readme(
            result=parse_result,
            original_name=f"{parse_result.metadata.get('title') or fallback_title}.md",
        )
        parse_payload_json = json.dumps(_build_material_ai_parse_payload(parse_result), ensure_ascii=False)

        if (
            normalized_mode == "optimize"
            and material["node_type"] == "file"
            and str(material.get("preview_type") or "") == "markdown"
        ):
            with get_db_connection() as conn:
                conn.execute(
                    """
                    UPDATE course_materials
                    SET ai_optimize_status = 'completed',
                        ai_optimized_markdown = ?,
                        ai_parse_status = 'completed',
                        ai_parse_result_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (markdown_content, parse_payload_json, datetime.now().isoformat(), material_id),
                )
                conn.commit()
                item = _fetch_material_response_item(conn, material_id, user)
            return {
                "status": "success",
                "message": "AI 已优化材料，并生成可查看优化稿",
                "mode": normalized_mode,
                "material": item,
                "viewer_url": f"/materials/view/{material_id}?variant=optimized",
                "updated_existing": True,
            }

        target_parent_id = int(material["id"]) if material["node_type"] == "folder" else (
            int(material["parent_id"]) if material.get("parent_id") is not None else None
        )
        generated = await _create_generated_markdown_material(
            user=user,
            parent_id=target_parent_id,
            title=str(parse_result.metadata.get("title") or fallback_title),
            markdown_content=markdown_content,
            parse_payload_json=parse_payload_json,
            name_prefix="AI重生成" if normalized_mode == "regenerate" else "AI优化",
        )
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE course_materials SET ai_optimize_status = 'completed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), material_id),
            )
            conn.commit()
        return {
            "status": "success",
            "message": "AI 已生成新的 Markdown 材料",
            "mode": normalized_mode,
            "material": generated,
            "viewer_url": f"/materials/view/{generated['id']}",
            "updated_existing": False,
        }
    except Exception as exc:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE course_materials SET ai_optimize_status = 'failed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), material_id),
            )
            conn.commit()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(500, f"AI 材料处理失败: {exc}")


__all__ = [name for name in globals() if not name.startswith("__")]
