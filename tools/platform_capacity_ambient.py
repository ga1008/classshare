"""Explicit synthetic HTTP ambient traffic, separate from open-loop actions.

Each identity owns three single-flight, completion-timed tickers. This models
visible-page background API traffic; it does not run browser DOM or rendering.
The existing capacity entrypoint owns credentials and the network boundary.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import math
import time
from urllib.parse import urlsplit
import uuid


DEFAULT_INTERVALS = {"bell": 15.0, "blog": 60.0, "heartbeat": 60.0}


def exception_diagnostic(exc, *, target=None):
    """Bounded native cause structure; never copy messages, args or HTTP data.

    The socket helper's str(exc) is unsuitable for HTTP errors which may carry
    credentials, query strings or response payloads. Only typed codes and a
    loopback origin are collected here, including AnyIO ExceptionGroup leaves.
    """
    seen = set()

    def attribute(error, name):
        try:
            return getattr(error, name, None)
        except Exception:
            return None

    def visit(error, depth=0):
        kind = type(error).__name__[:80]
        if id(error) in seen:
            return {"error": kind, "cycle": True}
        if len(seen) >= 24:
            return {"truncated": True}
        seen.add(id(error))
        result = {"error": kind}
        for name in ("errno", "winerror"):
            value = attribute(error, name)
            result[name] = value if type(value) is int and -(2**31) <= value < 2**31 else None
        result["detail"] = result["error"] + "".join(
            f" {name}={result[name]}" for name in ("errno", "winerror") if result[name] is not None)
        if depth >= 6:
            result["truncated"] = True
            return result
        cause, context = attribute(error, "__cause__"), attribute(error, "__context__")
        if isinstance(cause, BaseException):
            result["cause"] = visit(cause, depth+1)
        if isinstance(context, BaseException) and context is not cause:
            result["context"] = visit(context, depth+1)
        if isinstance(error, BaseExceptionGroup):
            children = error.exceptions
            result["exceptions"] = [visit(child, depth+1) for child in children[:8]]
            if len(children) > 8:
                result["children_truncated"] = True
        return result

    result = visit(exc)
    result["target"] = None
    try:
        url = urlsplit(str(target))
        if url.scheme in {"http", "https", "ws", "wss"} and url.hostname in {"127.0.0.1", "::1", "localhost"} and url.port:
            host = "[::1]" if url.hostname == "::1" else url.hostname
            result["target"] = f"{url.scheme}://{host}:{url.port}"
    except (TypeError, ValueError):
        pass
    return result


def diagnostic_counts(counts):
    return [{"flow": flow, "error": error, "count": count}
            for (flow, error), count in sorted(counts.items())]


def _distribution(values):
    values = sorted(values)
    return ({"count": len(values), **{
        f"p{percent}_ms": round(values[min(len(values) - 1, int(len(values) * percent / 100))], 2)
        for percent in (50, 95, 99)}, "max_ms": round(values[-1], 2)} if values else {})


class AmbientLoad:
    def __init__(self, client, tokens, offering, *, synthetic=False, user_keys=None,
                 intervals=None, clock=time.perf_counter, wall_time=time.time):
        url = urlsplit(str(client.base_url))
        if (synthetic is not True or url.scheme not in {"http", "https"}
                or url.hostname not in {"127.0.0.1", "::1"} or url.port is None
                or not 1024 <= url.port <= 65535 or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("Ambient load requires the explicit synthetic loopback capacity client")
        if (not tokens or any(not isinstance(token, str) or not token or "\r" in token or "\n" in token for token in tokens)
                or len(set(tokens)) != len(tokens) or type(offering) is not int or offering <= 0):
            raise ValueError("Distinct synthetic identity tokens and a classroom ID are required")
        self.intervals = dict(DEFAULT_INTERVALS if intervals is None else intervals)
        if (self.intervals.keys() != DEFAULT_INTERVALS.keys()
                or any(not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
                       for value in self.intervals.values())):
            raise ValueError("All three ambient intervals must be positive finite seconds")
        if user_keys is not None and (len(user_keys) != len(tokens) or len(set(user_keys)) != len(tokens)
                or any(not isinstance(key, str) or not key.startswith("student:") or not key[8:].isdigit() for key in user_keys)):
            raise ValueError("Ambient user keys must match the distinct synthetic students")
        self.client, self.tokens, self.offering = client, list(tokens), offering
        self.user_keys = list(user_keys) if user_keys is not None else [None] * len(tokens)
        self.clock, self.wall_time = clock, wall_time
        self.run_key = uuid.uuid4().hex[:16]
        self.stop_event = asyncio.Event()
        self.tasks = []
        self.started_at = self.stop_requested_at = self.finished_at = None
        self.samples = defaultdict(list)
        self.statuses, self.requests, self.failures = Counter(), Counter(), Counter()
        self.fault_counts, self.exception_counts = Counter(), Counter()
        self.exception_details = []
        self.faults = []

    def start(self):
        if self.started_at is not None or self.stop_event.is_set():
            raise RuntimeError("Ambient load cannot be restarted")
        self.started_at = self.clock()
        for index in range(len(self.tokens)):
            for channel_index, channel in enumerate(DEFAULT_INTERVALS):
                phase = self.intervals[channel] * (index + channel_index / 3) / len(self.tokens)
                self.tasks.append(asyncio.create_task(self._ticker(index, channel, phase)))

    def request_stop(self):
        if self.stop_requested_at is None:
            self.stop_requested_at = self.clock()
        self.stop_event.set()

    async def stop(self):
        self.request_stop()
        # Never cancel an in-flight POST: its real client timeout governs the
        # drain, so an unknown acceptance is reported rather than abandoned.
        results = await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.finished_at is None:
            for result in results:
                if isinstance(result, BaseException):
                    self._fault("ambient-worker", type(result).__name__, exception=result)
            self.finished_at = self.clock()

    async def _wait(self, seconds):
        if self.stop_event.is_set():
            return True
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=max(0.0, seconds))
            return True
        except TimeoutError:
            return self.stop_event.is_set()

    def _fault(self, label, error, *, status=None, exception=None):
        self.failures[label] += 1
        self.fault_counts[(label, error)] += 1
        diagnostic = None
        if exception is not None:
            self.exception_counts[(label, type(exception).__name__)] += 1
            if len(self.exception_details) < 30 or len(self.faults) < 30:
                diagnostic = exception_diagnostic(exception, target=self.client.base_url)
            if len(self.exception_details) < 30:
                self.exception_details.append({"flow": label, **diagnostic})
        if len(self.faults) < 30:
            self.faults.append({"flow": label, "error": error, **({"status": status} if status is not None else {}),
                                **({"diagnostic": diagnostic} if diagnostic is not None else {})})

    async def _ticker(self, index, channel, phase):
        if await self._wait(self.started_at + phase - self.clock()):
            return
        sequence = 0
        label = "ambient-" + channel
        while not self.stop_event.is_set():
            sequence += 1
            started = self.clock()
            self.requests[label] += 1
            headers = {"Cookie": f"access_token={self.tokens[index]}"}
            batch_id = None
            response = None
            try:
                if channel == "heartbeat":
                    batch_id = f"b1_{int(self.wall_time() * 1000)}_ambient{self.run_key}_{index}_{sequence}"
                    body = {"batch_id": batch_id, "page_key": "capacity_ambient", "events": [{
                        "action_type": "presence_heartbeat", "summary_text": "", "payload": {
                            "visibility_state": "visible", "focused": True, "idle_seconds": 0, "ai_panel_open": False,
                        }}]}
                    if self.user_keys[index] is not None:
                        body["user_key"] = self.user_keys[index]
                    response = await self.client.post(f"/api/classrooms/{self.offering}/behavior/batch", headers=headers, json=body)
                else:
                    path = "/api/message-center/summary" if channel == "bell" else "/api/blog/summary"
                    response = await self.client.get(path, headers=headers)
                self.statuses[(label, response.status_code)] += 1
                if response.status_code not in ({200, 202} if channel == "heartbeat" else {200}):
                    self._fault(label, "unexpected_status", status=response.status_code)
                elif channel == "heartbeat":
                    receipt = response.json()
                    if (receipt.get("status") != "success" or receipt.get("batch_id") != batch_id
                            or receipt.get("write_mode") not in {"synced", "durable_queued"} or receipt.get("degraded")):
                        self._fault(label, "invalid_durable_receipt", status=response.status_code)
            except Exception as exc:
                if response is None:
                    self.statuses[(label, type(exc).__name__)] += 1
                self._fault(label, type(exc).__name__, exception=exc)
            finally:
                self.samples[label].append(max(0.0, self.clock() - started) * 1000)
            if await self._wait(self.intervals[channel]):
                return

    def snapshot(self):
        now = self.finished_at if self.finished_at is not None else self.clock()
        elapsed = max(0.0, now - self.started_at) if self.started_at is not None else 0.0
        scheduling = max(0.0, (self.stop_requested_at if self.stop_requested_at is not None else now) - self.started_at) if self.started_at is not None else 0.0
        completed = sum(len(values) for values in self.samples.values())
        return {"enabled": True, "identity_count": len(self.tokens), "ticker_count": len(self.tasks),
                "intervals_seconds": self.intervals, "timing": "uniform phase then completion plus interval; one in-flight per identity/flow",
                "elapsed_seconds": round(elapsed, 3), "scheduling_seconds": round(scheduling, 3),
                "drain_seconds": round(max(0.0, elapsed - scheduling), 3),
                "started_requests": sum(self.requests.values()), "completed_requests": completed,
                "actual_rps": round(completed / elapsed, 3) if elapsed else 0.0,
                "nominal_rps_before_response_delay": round(len(self.tokens) * sum(1 / value for value in self.intervals.values()), 3),
                "actual_rps_by_flow": {label: round(len(values) / elapsed, 3) if elapsed else 0.0
                                       for label, values in self.samples.items()},
                "requests_by_flow": dict(self.requests), "error_count": sum(self.failures.values()),
                "fault_count": sum(self.fault_counts.values()), "fault_counts": diagnostic_counts(self.fault_counts),
                "exception_count": sum(self.exception_counts.values()), "exception_counts": diagnostic_counts(self.exception_counts),
                "exception_details": list(self.exception_details), "exception_details_limit": 30,
                "latencies": {label: _distribution(values) for label, values in self.samples.items()},
                "statuses": [{"flow": label, "status": status, "count": count}
                             for (label, status), count in self.statuses.items()], "faults": list(self.faults),
                "scope": "synthetic HTTP visible-page ambient APIs; excludes browser DOM/rendering"}
