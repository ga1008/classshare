"""Preserve the normal HTTP client; trusted AI-account work uses the gateway."""
import httpx
import asyncio
from contextlib import asynccontextmanager


class AIBudgetAwareClient(httpx.AsyncClient):
    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        from .wanderer_ai_attribution import current_attribution, reserve_call
        attribution = current_attribution()
        if attribution is not None and str(method).upper() == "POST":
            logical_id = attribution.next_call(str(url), kwargs.get("json"))
            await asyncio.to_thread(reserve_call, attribution, logical_id)
        async with super().stream(method, url, **kwargs) as response:
            yield response

    async def _post_without_gateway(self, url, **kwargs):
        return await super().post(url, **kwargs)

    async def post(self, url, **kwargs):
        from .wanderer_ai_attribution import current_attribution
        if current_attribution() is None:
            return await self._post_without_gateway(url, **kwargs)
        from .ai_gateway_service import ai_gateway_post
        payload = kwargs.pop("json", None)
        return await ai_gateway_post(self, str(url), json_payload=payload,
            task_type=str((payload or {}).get("task_type") or "wanderer_platform_tool") if isinstance(payload, dict) else "wanderer_platform_tool",
            **kwargs)
