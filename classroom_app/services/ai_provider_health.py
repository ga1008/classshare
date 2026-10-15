"""Provider circuit breaker behind cross-vendor AI failover (2026-10-15).

One registry lives in the AI service process. Every provider attempt reports
success or a classified failure; outage-class failures (no response, timeout,
5xx/429) open the provider's breaker after ``failure_threshold`` hits inside
``window_seconds``. While a breaker is open the router sends work to the peer
vendor's equivalent tier first. After ``cooldown_seconds`` the breaker goes
half-open: exactly one real call (or the background probe) is allowed through;
success closes it again, failure re-opens it with a doubled cooldown (capped).

Local network trouble is not a vendor outage: a connection-class failure is
ignored for tripping when the other enabled vendor also failed to connect in
the same window, because switching vendors cannot help there.

The registry is in-memory and lock-protected; it never touches the database.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

FAILURE_KIND_CONNECTION = "connection"  # no HTTP response: DNS/connect/network error
FAILURE_KIND_TIMEOUT = "timeout"        # request sent, no answer in time
FAILURE_KIND_SERVER = "server"          # 5xx answer: the vendor is failing
FAILURE_KIND_THROTTLED = "throttled"    # 429: busy, not down (retry with Retry-After first)
FAILURE_KIND_REJECTED = "rejected"      # 4xx request rejection: our request, not their outage
FAILURE_KIND_OTHER = "other"            # parse/format/content: not an outage
# Kinds that count toward opening the breaker.
OUTAGE_KINDS = frozenset({FAILURE_KIND_CONNECTION, FAILURE_KIND_TIMEOUT, FAILURE_KIND_SERVER, FAILURE_KIND_THROTTLED})
# Kinds where a call switches vendor at once instead of retrying the same one.
IMMEDIATE_FAILOVER_KINDS = frozenset({FAILURE_KIND_CONNECTION, FAILURE_KIND_TIMEOUT, FAILURE_KIND_SERVER})
# Kinds where the provider certainly did not generate anything (not billable).
UNBILLED_KINDS = frozenset({FAILURE_KIND_CONNECTION, FAILURE_KIND_SERVER, FAILURE_KIND_THROTTLED, FAILURE_KIND_REJECTED})

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


def _wall_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProviderState:
    provider: str
    state: str = STATE_CLOSED
    consecutive_failures: int = 0
    failure_times: list[float] = field(default_factory=list)
    trips: int = 0
    opened_at: float | None = None
    open_until: float | None = None
    cooldown_seconds: float = 0.0
    probe_in_flight: bool = False
    manual: bool = False
    network_suspected: bool = False
    last_error: str = ""
    last_error_kind: str = ""
    last_error_at: str = ""
    last_success_at: str = ""
    last_state_change_at: str = ""
    last_state_reason: str = ""
    failovers_out: int = 0
    served_as_failover: int = 0


class ProviderHealthRegistry:
    def __init__(
        self,
        providers: Iterable[str] = (),
        *,
        failure_threshold: int = 2,
        window_seconds: float = 300.0,
        cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], str] = _wall_now,
        on_change: Callable[[str, str, str, str], None] | None = None,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.window_seconds = max(1.0, float(window_seconds))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self.max_cooldown_seconds = max(self.cooldown_seconds, float(max_cooldown_seconds))
        self._clock = clock
        self._wall = wall
        self._on_change = on_change
        self._lock = threading.RLock()
        self._states: dict[str, ProviderState] = {name: ProviderState(name) for name in providers}

    # ------------------------------------------------------------------ helpers
    def _state(self, provider: str) -> ProviderState:
        state = self._states.get(provider)
        if state is None:
            state = self._states[provider] = ProviderState(provider)
        return state

    def _transition(self, state: ProviderState, new_state: str, reason: str) -> None:
        old = state.state
        if old == new_state:
            return
        state.state = new_state
        state.last_state_change_at = self._wall()
        state.last_state_reason = reason
        if self._on_change:
            try:
                self._on_change(state.provider, old, new_state, reason)
            except Exception as exc:  # noqa: BLE001 - observers never break routing
                print(f"[AI HEALTH] on_change observer failed: {exc!r}")

    def _tick_locked(self, state: ProviderState, now: float) -> None:
        if state.state == STATE_OPEN and state.open_until is not None and now >= state.open_until:
            state.probe_in_flight = False
            self._transition(state, STATE_HALF_OPEN, "cooldown elapsed; probing")

    def _prune_locked(self, state: ProviderState, now: float) -> None:
        cutoff = now - self.window_seconds
        state.failure_times = [stamp for stamp in state.failure_times if stamp >= cutoff]

    def _peer_connection_failure_recent(self, provider: str, now: float) -> bool:
        cutoff = now - self.window_seconds
        for name, other in self._states.items():
            if name == provider:
                continue
            if other.last_error_kind == FAILURE_KIND_CONNECTION and any(stamp >= cutoff for stamp in other.failure_times):
                return True
        return False

    def _open_locked(self, state: ProviderState, now: float, reason: str, *, manual: bool = False,
                     cooldown: float | None = None) -> None:
        state.trips += 1
        if cooldown is None:
            cooldown = min(self.max_cooldown_seconds, self.cooldown_seconds * (2 ** max(0, state.trips - 1)))
        state.cooldown_seconds = float(cooldown)
        state.opened_at = now
        state.open_until = now + float(cooldown)
        state.probe_in_flight = False
        state.manual = manual
        self._transition(state, STATE_OPEN, reason)

    # ------------------------------------------------------------------ reporting
    def record_success(self, provider: str) -> None:
        with self._lock:
            state = self._state(provider)
            self._tick_locked(state, self._clock())
            if state.state == STATE_OPEN:
                # A call that started before the trip finished late; recovery is
                # decided by the half-open probe, and manual trips stay put.
                state.last_success_at = self._wall()
                return
            state.consecutive_failures = 0
            state.failure_times = []
            state.probe_in_flight = False
            state.network_suspected = False
            state.last_success_at = self._wall()
            if state.state != STATE_CLOSED:
                state.trips = 0
                state.opened_at = state.open_until = None
                state.manual = False
                self._transition(state, STATE_CLOSED, "call succeeded; recovered")

    def record_failure(self, provider: str, kind: str, error: Any = "") -> bool:
        """Record a failure; return True when this call tripped the breaker open."""
        now = self._clock()
        with self._lock:
            state = self._state(provider)
            self._tick_locked(state, now)
            state.last_error = str(error)[:400]
            state.last_error_kind = kind
            state.last_error_at = self._wall()
            if kind not in OUTAGE_KINDS:
                return False
            state.consecutive_failures += 1
            state.failure_times.append(now)
            self._prune_locked(state, now)
            if state.state == STATE_HALF_OPEN:
                state.probe_in_flight = False
                self._open_locked(state, now, f"probe failed ({kind})")
                return True
            if state.state == STATE_OPEN:
                return False
            if kind == FAILURE_KIND_CONNECTION and self._peer_connection_failure_recent(provider, now):
                # Both vendors unreachable: that is our side of the network.
                state.network_suspected = True
                return False
            state.network_suspected = False
            if len(state.failure_times) >= self.failure_threshold:
                self._open_locked(state, now, f"{len(state.failure_times)} outage-class failures within {int(self.window_seconds)}s ({kind})")
                return True
            return False

    def note_failover(self, from_provider: str, to_provider: str) -> None:
        with self._lock:
            self._state(from_provider).failovers_out += 1
            self._state(to_provider).served_as_failover += 1

    # ------------------------------------------------------------------ control
    def trip(self, provider: str, reason: str = "manual", *, cooldown_seconds: float | None = None) -> None:
        with self._lock:
            state = self._state(provider)
            state.trips = 0
            self._open_locked(state, self._clock(), reason, manual=True,
                              cooldown=cooldown_seconds if cooldown_seconds is not None else self.max_cooldown_seconds)

    def reset(self, provider: str, reason: str = "manual reset") -> None:
        with self._lock:
            state = self._state(provider)
            state.consecutive_failures = 0
            state.failure_times = []
            state.trips = 0
            state.opened_at = state.open_until = None
            state.probe_in_flight = False
            state.manual = False
            state.network_suspected = False
            self._transition(state, STATE_CLOSED, reason)

    def reset_all(self, reason: str = "reset all") -> None:
        """Forget every failure and close every breaker (tests, manual recovery)."""
        with self._lock:
            for provider in list(self._states):
                self.reset(provider, reason)

    # ------------------------------------------------------------------ routing queries
    def state_of(self, provider: str) -> str:
        with self._lock:
            state = self._state(provider)
            self._tick_locked(state, self._clock())
            return state.state

    def is_available(self, provider: str) -> bool:
        """Closed, or half-open with the probe slot free."""
        with self._lock:
            state = self._state(provider)
            self._tick_locked(state, self._clock())
            return state.state == STATE_CLOSED or (state.state == STATE_HALF_OPEN and not state.probe_in_flight)

    def acquire_probe(self, provider: str) -> bool:
        """Claim the single half-open probe slot; True when this call is the probe."""
        with self._lock:
            state = self._state(provider)
            self._tick_locked(state, self._clock())
            if state.state == STATE_HALF_OPEN and not state.probe_in_flight:
                state.probe_in_flight = True
                return True
            return False

    def release_probe(self, provider: str) -> None:
        with self._lock:
            self._state(provider).probe_in_flight = False

    def probe_due(self) -> list[str]:
        """Providers whose cooldown elapsed and that nobody is probing right now."""
        with self._lock:
            now = self._clock()
            due = []
            for state in self._states.values():
                self._tick_locked(state, now)
                if state.state == STATE_HALF_OPEN and not state.probe_in_flight:
                    due.append(state.provider)
            return due

    def rank(self, provider: str) -> int:
        """0 healthy, 1 half-open (probe free), 2 open or probe busy. Lower goes first."""
        with self._lock:
            state = self._state(provider)
            self._tick_locked(state, self._clock())
            if state.state == STATE_CLOSED:
                return 0
            if state.state == STATE_HALF_OPEN and not state.probe_in_flight:
                return 1
            return 2

    def order(self, providers: list[str]) -> list[str]:
        """Stable health ordering: healthy first, then probe-able, then open."""
        return sorted(providers, key=lambda name: self.rank(name))

    # ------------------------------------------------------------------ observability
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            providers: dict[str, Any] = {}
            for name, state in self._states.items():
                self._tick_locked(state, now)
                self._prune_locked(state, now)
                providers[name] = {
                    "state": state.state,
                    "available": state.state == STATE_CLOSED or (state.state == STATE_HALF_OPEN and not state.probe_in_flight),
                    "consecutive_failures": state.consecutive_failures,
                    "recent_failures": len(state.failure_times),
                    "trips": state.trips,
                    "cooldown_seconds": round(state.cooldown_seconds, 1),
                    "retry_in_seconds": (max(0.0, round(state.open_until - now, 1)) if state.open_until is not None and state.state == STATE_OPEN else 0.0),
                    "probe_in_flight": state.probe_in_flight,
                    "manual": state.manual,
                    "network_suspected": state.network_suspected,
                    "last_error": state.last_error,
                    "last_error_kind": state.last_error_kind,
                    "last_error_at": state.last_error_at,
                    "last_success_at": state.last_success_at,
                    "last_state_change_at": state.last_state_change_at,
                    "last_state_reason": state.last_state_reason,
                    "failovers_out": state.failovers_out,
                    "served_as_failover": state.served_as_failover,
                }
            return {
                "policy": {
                    "failure_threshold": self.failure_threshold,
                    "window_seconds": self.window_seconds,
                    "cooldown_seconds": self.cooldown_seconds,
                    "max_cooldown_seconds": self.max_cooldown_seconds,
                },
                "providers": providers,
                "degraded": any(item["state"] != STATE_CLOSED for item in providers.values()),
            }
