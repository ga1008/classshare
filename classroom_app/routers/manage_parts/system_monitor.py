"""Super-admin server monitor dashboard APIs (监控大屏)."""

from fastapi import Body
from starlette.concurrency import run_in_threadpool

from .common import *
from ...services.server_monitor_service import (
    AI_INSIGHT_SYSTEM_PROMPT,
    ProcessActionError,
    build_ai_insight_payload,
    build_monitor_snapshot,
    build_process_tree,
    optimize_memory,
    terminate_process,
)


router = APIRouter()

AI_INSIGHT_TIMEOUT_SECONDS = 30.0


@router.get("/system/monitor/snapshot", response_class=JSONResponse)
async def api_get_monitor_snapshot(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
    snapshot = await run_in_threadpool(build_monitor_snapshot)
    return {"status": "success", "snapshot": snapshot}


@router.get("/system/monitor/processes", response_class=JSONResponse)
async def api_get_monitor_processes(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
    tree = await run_in_threadpool(build_process_tree)
    return {"status": "success", **tree}


@router.post("/system/monitor/processes/{pid:int}/terminate", response_class=JSONResponse)
async def api_terminate_monitor_process(
    pid: int,
    payload: dict = Body(default={}),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
    force = bool((payload or {}).get("force"))
    try:
        result = await run_in_threadpool(lambda: terminate_process(pid, force=force))
    except ProcessActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    action = "强制终止" if force else "终止"
    print(f"[MONITOR] 超管 {user.get('id')} {action}进程 {result['pid']}（{result['name']}）")
    return {"status": "success", "result": result}


@router.post("/system/monitor/memory/optimize", response_class=JSONResponse)
async def api_optimize_monitor_memory(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
    result = await run_in_threadpool(optimize_memory)
    print(f"[MONITOR] 超管 {user.get('id')} 触发内存优化：{result}")
    return {"status": "success", "result": result}


@router.post("/system/monitor/ai-insight", response_class=JSONResponse)
async def api_monitor_ai_insight(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)

    snapshot = await run_in_threadpool(build_monitor_snapshot)
    digest = build_ai_insight_payload(snapshot)
    payload = {
        "system_prompt": AI_INSIGHT_SYSTEM_PROMPT,
        "messages": [],
        "new_message": json.dumps(digest, ensure_ascii=False),
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "interactive",
        "task_label": "server_monitor_insight",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=AI_INSIGHT_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务暂不可用：{exc}") from exc

    parsed = data.get("response_json") if isinstance(data, dict) else None
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="AI 返回格式异常，请稍后重试。")

    def _clean_items(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:4]

    try:
        health_score = max(0, min(100, int(parsed.get("health_score") or 0)))
    except (TypeError, ValueError):
        health_score = 0

    insight = {
        "summary": str(parsed.get("summary") or "").strip(),
        "health_score": health_score,
        "highlights": _clean_items(parsed.get("highlights")),
        "risks": _clean_items(parsed.get("risks")),
        "suggestions": _clean_items(parsed.get("suggestions")),
        "generated_at": snapshot.get("generated_at", ""),
    }
    return {"status": "success", "insight": insight}
