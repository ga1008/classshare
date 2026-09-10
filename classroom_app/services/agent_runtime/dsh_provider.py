"""Task-bound DSH ACP adapter. It never authorizes or commits platform actions."""

import asyncio
import contextlib
import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .acp_client import AcpStdioClient
from .contracts import AcpClientOptions, AcpProtocolError, AcpTransportClosed


@dataclass(frozen=True)
class DshRunIdentity:
    task_id: str
    actor_id: str
    attempt_id: str
    fencing_token: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.task_id, self.actor_id, self.attempt_id, self.fencing_token)):
            raise ValueError("DSH task, actor, attempt and fencing identities are required")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*:[^\s:]+", self.actor_id):
            raise ValueError("DSH actor identity must use role:id")


@dataclass(frozen=True)
class DshRuntimeEvidence:
    """Verified by the launcher/image build, not the ACP agentInfo version."""
    dsh_package_version: str
    profile_sha256: str

    def __post_init__(self):
        if not self.dsh_package_version or not re.fullmatch(r"[0-9a-f]{64}", self.profile_sha256):
            raise ValueError("DSH package version and profile SHA-256 are required")


@dataclass(frozen=True)
class DshSessionRef:
    identity: DshRunIdentity
    runtime_session_id: str

    def validate(self, expected: DshRunIdentity):
        if self.identity != expected or not isinstance(self.runtime_session_id, str) or not self.runtime_session_id:
            raise ValueError("DSH session does not belong to this task, actor, attempt and fence")


@dataclass(frozen=True)
class DshRuntimeEvent:
    identity: DshRunIdentity
    runtime_evidence: DshRuntimeEvidence
    runtime_session_id: str
    sequence: int
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class DshRunResult:
    session_ref: DshSessionRef
    runtime_evidence: DshRuntimeEvidence
    stop_reason: str
    final_text: str
    tool_receipts: tuple[dict[str, Any], ...]
    usage_updates: tuple[dict[str, Any], ...]
    submitted: bool
    source: str = "acp"
    reasoning_effort: str | None = None

    @property
    def completed(self) -> bool:
        return self.submitted and self.stop_reason == "end_turn"


EventHandler = Callable[[DshRuntimeEvent], Awaitable[None]]
PermissionHandler = Callable[[DshRunIdentity, dict[str, Any]], Awaitable[dict[str, Any]]]


class DshProvider:
    """One trusted launcher command and one owned session per task attempt.

    Session references must come from the platform ledger, never request JSON.
    A new attempt cannot resume an old reference without platform authorization
    and explicit rebinding. ACP tool receipts describe runtime execution only;
    operation ids, commit state and authorization remain Broker responsibilities.
    """

    def __init__(self, identity: DshRunIdentity, client_options: AcpClientOptions, *,
                 runtime_evidence: DshRuntimeEvidence,
                 runtime_cwd: str | None = None,
                 reasoning_effort: str | None = None,
                 mcp_servers: list[dict[str, Any]] | None = None,
                 permission_handler: PermissionHandler | None = None,
                 prompt_timeout: float = 900.0, event_timeout: float = 30.0,
                 cancel_timeout: float = 10.0, max_result_bytes: int = 8 * 1024 * 1024):
        for value in (prompt_timeout, event_timeout, cancel_timeout):
            if not 0 < value < float("inf"):
                raise ValueError("DSH timeouts must be finite and positive")
        self.identity = identity
        self.runtime_evidence = runtime_evidence
        self.options = client_options
        # A launcher subprocess runs in the worker filesystem; ACP addresses the
        # runner filesystem, which can be a different container namespace.
        if runtime_cwd is not None and (not isinstance(runtime_cwd, str) or not runtime_cwd.strip()):
            raise ValueError("DSH runtime working directory is required")
        self.runtime_cwd = runtime_cwd or str(client_options.cwd)
        if reasoning_effort is not None and reasoning_effort not in {"off", "low", "high", "max"}:
            raise ValueError("DSH reasoning effort must be off, low, high or max")
        self.reasoning_effort = reasoning_effort
        self._config_options: list[dict] = []
        self.mcp_servers = copy.deepcopy(mcp_servers or [])
        self._permission_handler = permission_handler
        self.prompt_timeout = prompt_timeout
        self.event_timeout = event_timeout
        self.cancel_timeout = cancel_timeout
        if isinstance(max_result_bytes, bool) or not isinstance(max_result_bytes, int) or max_result_bytes <= 0:
            raise ValueError("DSH result byte limit must be a positive integer")
        self.max_result_bytes = max_result_bytes
        self._retained_bytes = 0
        self.client = AcpStdioClient(client_options, request_handler=self._permission)
        self._capabilities: dict | None = None
        self._start_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._session_ref: DshSessionRef | None = None
        self._pump: asyncio.Task | None = None
        self._prompt: asyncio.Task | None = None
        self._on_event: EventHandler | None = None
        self._processed = 0
        self._progress = asyncio.Event()
        self._pump_error: Exception | None = None
        self._cancel_requested = False
        self._closing = False
        self._text: list[str] = []
        self._receipts: dict[str, dict] = {}
        self._usage: list[dict] = []

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self) -> dict:
        async with self._start_lock:
            if self._closing:
                raise AcpTransportClosed("DSH provider is closed")
            if self._capabilities is not None:
                return copy.deepcopy(self._capabilities)
            try:
                value = await self.client.request("initialize", {
                    "protocolVersion": 1, "clientCapabilities": {},
                    "clientInfo": {"name": "lanshare-agent", "version": "1"},
                })
                if not isinstance(value, dict) or value.get("protocolVersion") != 1:
                    raise AcpProtocolError("DSH requires ACP protocol version 1")
                capabilities = value.get("agentCapabilities")
                if not isinstance(capabilities, dict):
                    raise AcpProtocolError("DSH handshake omitted capabilities")
                self._capabilities = copy.deepcopy(value)
                self._pump = asyncio.create_task(self._consume_events())
                return copy.deepcopy(value)
            except BaseException:
                await self.client.close()
                raise

    async def _session(self, session_ref: DshSessionRef | None):
        if session_ref:
            session_ref.validate(self.identity)
        if self._session_ref:
            if session_ref and session_ref != self._session_ref:
                raise ValueError("DSH provider already owns a different session")
            return
        params = {"cwd": self.runtime_cwd, "mcpServers": self.mcp_servers}
        if session_ref:
            if "resume" not in self._capabilities["agentCapabilities"].get("sessionCapabilities", {}):
                raise AcpProtocolError("DSH runtime does not advertise session resume")
            params["sessionId"] = session_ref.runtime_session_id
            self._session_ref = session_ref
            try:
                result = await self.client.request("session/resume", params)
            except BaseException:
                self._session_ref = None
                raise
        else:
            result = await self.client.request("session/new", params)
            session_id = result.get("sessionId") if isinstance(result, dict) else None
            if not isinstance(session_id, str) or not session_id:
                raise AcpProtocolError("DSH session/new omitted sessionId")
            self._session_ref = DshSessionRef(self.identity, session_id)
        options = result.get("configOptions", []) if isinstance(result, dict) else []
        self._config_options = copy.deepcopy(options) if isinstance(options, list) else []
        if self.reasoning_effort is not None:
            try:
                await self._set_reasoning(self.reasoning_effort)
            except BaseException:
                failed = self._session_ref
                self._session_ref = None
                self._config_options = []
                with contextlib.suppress(Exception):
                    await self.client.request("session/close", {"sessionId": failed.runtime_session_id})
                raise

    async def _set_reasoning(self, value: str) -> list[dict]:
        option = next((item for item in self._config_options
                       if isinstance(item, dict) and item.get("id") == "reasoning_effort"), None)
        if not option or not any(isinstance(item, dict) and item.get("value") == value
                                 for item in option.get("options", [])):
            raise AcpProtocolError("DSH does not advertise the requested reasoning effort")
        result = await self.client.request("session/set_config_option", {
            "sessionId": self._session_ref.runtime_session_id, "configId": "reasoning_effort", "value": value,
        })
        options = result.get("configOptions") if isinstance(result, dict) else None
        if not isinstance(options, list) or not any(isinstance(item, dict)
                and item.get("id") == "reasoning_effort" and item.get("currentValue") == value for item in options):
            raise AcpProtocolError("DSH did not confirm the requested reasoning effort")
        self._config_options = copy.deepcopy(options)
        self.reasoning_effort = value
        return copy.deepcopy(options)

    async def configure_reasoning(self, value: str) -> list[dict]:
        """Apply one advertised effort between prompts; never changes the model."""
        if value not in {"off", "low", "high", "max"}:
            raise ValueError("DSH reasoning effort must be off, low, high or max")
        async with self._run_lock:
            await self.start()
            await self._session(None)
            return await self._set_reasoning(value)

    async def run(self, prompt: list[dict[str, Any]], *, session_ref: DshSessionRef | None = None,
                  on_event: EventHandler | None = None) -> DshRunResult:
        if session_ref:
            session_ref.validate(self.identity)
        if not isinstance(prompt, list) or not prompt:
            raise ValueError("DSH prompt content is required")
        async with self._run_lock:
            self._cancel_requested = False
            await self.start()
            await self._session(session_ref)
            await self._drain(self.client.notification_sequence)
            self._text, self._receipts, self._usage = [], {}, []
            self._retained_bytes = 0
            self._on_event = on_event
            if self._cancel_requested:
                self._on_event = None
                return self._result("cancelled", submitted=False)
            try:
                self._prompt = asyncio.create_task(self.client.request("session/prompt", {
                    "sessionId": self._session_ref.runtime_session_id,
                    "prompt": copy.deepcopy(prompt),
                }, timeout=self.prompt_timeout))
                result = await asyncio.shield(self._prompt)
                await self._drain(self.client.notification_sequence)
                reason = result.get("stopReason") if isinstance(result, dict) else None
                if reason not in {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}:
                    raise AcpProtocolError("DSH prompt returned an unknown stopReason")
                return self._result(reason, submitted=True)
            except BaseException:
                with contextlib.suppress(Exception):
                    await self.cancel()
                raise
            finally:
                self._prompt = None
                self._on_event = None

    def _result(self, reason: str, *, submitted: bool) -> DshRunResult:
        return DshRunResult(self._session_ref, self.runtime_evidence, reason, "".join(self._text),
                            tuple(copy.deepcopy(list(self._receipts.values()))),
                            tuple(copy.deepcopy(self._usage)), submitted, reasoning_effort=self.reasoning_effort)

    async def _permission(self, method: str, params: Any) -> dict:
        denied = {"outcome": {"outcome": "cancelled"}}
        if (method != "session/request_permission" or not isinstance(params, dict)
                or self._session_ref is None or params.get("sessionId") != self._session_ref.runtime_session_id
                or self._permission_handler is None or self._cancel_requested or self._closing):
            return denied
        result = await self._permission_handler(self.identity, copy.deepcopy(params))
        if self._cancel_requested or self._closing:
            return denied
        outcome = result.get("outcome") if isinstance(result, dict) else None
        if not isinstance(outcome, dict):
            return denied
        if outcome.get("outcome") == "cancelled":
            return denied
        options = params.get("options", [])
        # The platform may select only a one-shot choice actually offered by DSH.
        chosen = next((option for option in options if isinstance(option, dict)
                       and option.get("optionId") == outcome.get("optionId")), None)
        if outcome.get("outcome") != "selected" or not chosen or chosen.get("kind") not in {"allow_once", "reject_once"}:
            return denied
        return {"outcome": {"outcome": "selected", "optionId": chosen["optionId"]}}

    async def _consume_events(self):
        try:
            while True:
                notification = await self.client.next_notification()
                try:
                    params = notification.params
                    if (notification.method == "session/update" and isinstance(params, dict)
                            and self._session_ref is not None
                            and params.get("sessionId") == self._session_ref.runtime_session_id):
                        update = params.get("update")
                        if not isinstance(update, dict):
                            raise AcpProtocolError("DSH session update is invalid")
                        event_type = self._project(update)
                        if self._on_event:
                            event = DshRuntimeEvent(self.identity, self.runtime_evidence, self._session_ref.runtime_session_id,
                                                    notification.sequence, event_type, copy.deepcopy(update))
                            await asyncio.wait_for(self._on_event(event), self.event_timeout)
                finally:
                    self._processed = notification.sequence
                    self._progress.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._pump_error = exc
            self._progress.set()
            if not self._closing:
                await self.client.close()

    def _project(self, update: dict) -> str:
        self._retained_bytes += len(json.dumps(update, ensure_ascii=False).encode("utf-8"))
        if self._retained_bytes > self.max_result_bytes:
            raise AcpProtocolError("DSH result/event interval byte limit exceeded")
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content", {})
            if isinstance(content, dict) and content.get("type") == "text" and isinstance(content.get("text"), str):
                self._text.append(content["text"])
            return "assistant_text"
        if kind in {"tool_call", "tool_call_update"}:
            call_id = update.get("toolCallId")
            if not isinstance(call_id, str) or not call_id:
                raise AcpProtocolError("DSH tool update omitted toolCallId")
            receipt = self._receipts.setdefault(call_id, {"tool_call_id": call_id, "source": "acp"})
            for field in ("status", "title", "kind", "content", "rawInput", "rawOutput"):
                if field in update:
                    receipt[field] = copy.deepcopy(update[field])
            return "tool_result" if kind == "tool_call_update" else "tool_start"
        if kind in {"usage_update", "context_usage_update"}:
            self._usage.append(copy.deepcopy(update))
            return "usage"
        return "runtime_update"

    async def _drain(self, through: int):
        async def wait():
            while self._processed < through:
                if self._pump_error:
                    raise self._pump_error
                self._progress.clear()
                await self._progress.wait()
            if self._pump_error:
                raise self._pump_error
        await asyncio.wait_for(wait(), self.event_timeout)

    async def cancel(self):
        self._cancel_requested = True
        if self._session_ref and not self._closing:
            await self.client.cancel_session(self._session_ref.runtime_session_id)
        if self._prompt and not self._prompt.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._prompt), self.cancel_timeout)
            except Exception:
                await self.client.close()

    async def close(self):
        if self._closing:
            await self.client.close()
            return
        try:
            await self.cancel()
            if self._session_ref:
                with contextlib.suppress(Exception):
                    await self.client.request("session/close", {"sessionId": self._session_ref.runtime_session_id}, timeout=self.cancel_timeout)
        finally:
            self._closing = True
            await self.client.close()
            if self._pump:
                self._pump.cancel()
                await asyncio.gather(self._pump, return_exceptions=True)
