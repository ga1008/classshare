"""HTTP boundary for task-scoped tool budgets; no domain work holds its DB lock."""
import asyncio

from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ..database import get_db_connection
from .agent_delegation_service import verify_task_delegation
from .agent_request_budget_service import reserve_agent_request_budget, finish_agent_request_budget, renew_agent_request_budget

TOOL_HTTP_TIMEOUT_SECONDS = 40
_INFLIGHT_TOOL_WORK: set[asyncio.Task] = set()


def reserve_tool_http_budget(token, channel="tools"):
    with get_db_connection() as conn:
        grant = verify_task_delegation(conn, token, purpose="tools",
                                      required_scope="web:fetch" if channel == "web" else "platform:read")
        lease = reserve_agent_request_budget(conn, grant=grant, channel=channel)
        conn.commit()
        return lease


def finish_tool_http_budget(lease, status):
    with get_db_connection() as conn:
        finish_agent_request_budget(conn, lease.id, status=status)
        conn.commit()


def _renew_tool_budget(lease):
    with get_db_connection() as conn:
        renewed = renew_agent_request_budget(conn, lease.id)
        conn.commit()
        return renewed


async def _hold_budget(lease):
    while True:
        await asyncio.sleep(10)
        if not await run_in_threadpool(_renew_tool_budget, lease):
            return


async def _execute_budgeted(endpoint, request, lease):
    renewal = asyncio.create_task(_hold_budget(lease))
    status = "failed"
    stopped = False
    try:
        result = await endpoint(request)
        stopped = True
        status = "completed" if result.status_code < 400 else "failed"
        return result
    except asyncio.CancelledError:
        # Process shutdown can cancel this task while a Python thread is still
        # running. Leave its lease occupied until expiry; do not claim it stopped.
        raise
    except BaseException:
        stopped = True
        raise
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)
        if stopped:
            await asyncio.shield(run_in_threadpool(finish_tool_http_budget, lease, status))


def _work_finished(task):
    _INFLIGHT_TOOL_WORK.discard(task)
    if not task.cancelled():
        task.exception()  # Retrieve failures even when the HTTP client has gone.


class AgentToolBudgetRoute(APIRoute):
    def get_route_handler(self):
        endpoint = super().get_route_handler()

        async def handler(request):
            authorization = request.headers.get("authorization", "")
            if not authorization.lower().startswith("bearer lsagt_"):
                raise HTTPException(401, "需要有效的任务授权。")
            token = authorization[7:].strip()
            lease = await run_in_threadpool(reserve_tool_http_budget, token)
            work = asyncio.create_task(_execute_budgeted(endpoint, request, lease))
            _INFLIGHT_TOOL_WORK.add(work)
            work.add_done_callback(_work_finished)
            # asyncio.wait never cancels the work on timeout or disconnect.
            # A lost response may conceal a committed operation: its caller
            # retries with the same operation_id to retrieve the durable receipt.
            completed, _ = await asyncio.wait({work}, timeout=TOOL_HTTP_TIMEOUT_SECONDS)
            if not completed:
                raise HTTPException(504, "平台工具响应超时；操作可能仍在完成，请使用相同操作编号查询回执。")
            return work.result()

        return handler
