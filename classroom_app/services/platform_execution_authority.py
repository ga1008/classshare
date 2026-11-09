"""Authorization adapters for one shared platform HTTP execution path."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import HTTPException


@dataclass(frozen=True)
class WandererServiceAuthority:
    wanderer_id: int
    turn_id: str
    life_epoch: int
    fencing_token: int
    authority_version: int | None = None
    conn_factory: Callable | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class PlatformAuthority:
    actor: Any
    principal_kind: str
    task_id: int | None
    attempt_id: str | None
    fencing_token: int
    delegation_id: str | None
    source_session_hash: str | None
    created_at: int
    wanderer_id: int | None = None
    turn_id: str | None = None
    life_epoch: int | None = None
    authority_version: int | None = None
    original_grant: Any = field(default=None, repr=False)

    @property
    def task(self):
        if self.original_grant is None:
            raise AttributeError("A service principal has no human Agent task")
        return self.original_grant.task

    @property
    def attempt(self):
        if self.original_grant is None:
            raise AttributeError("A service principal has no human Agent attempt")
        return self.original_grant.attempt

    @property
    def delegation(self):
        if self.original_grant is None:
            raise AttributeError("A service principal has no human Agent delegation")
        return self.original_grant.delegation

    def same_source(self, other: "PlatformAuthority") -> bool:
        return (self.principal_kind, self.actor.key, self.actor.authority_fingerprint, self.task_id,
                self.attempt_id, self.fencing_token, self.delegation_id, self.source_session_hash,
                self.wanderer_id, self.turn_id, self.life_epoch, self.authority_version) == (
                other.principal_kind, other.actor.key, other.actor.authority_fingerprint, other.task_id,
                other.attempt_id, other.fencing_token, other.delegation_id, other.source_session_hash,
                other.wanderer_id, other.turn_id, other.life_epoch, other.authority_version)

    def matches_receipt(self, row) -> bool:
        return (row["principal_kind"] == self.principal_kind and row["task_id"] == self.task_id
                and row["source_session_hash"] == self.source_session_hash
                and row["wanderer_id"] == self.wanderer_id and row["turn_id"] == self.turn_id
                and row["life_epoch"] == self.life_epoch and row["authority_version"] == self.authority_version
                and row["authority_fingerprint"] == self.actor.authority_fingerprint)


def authority_connection(source):
    if isinstance(source, WandererServiceAuthority) and source.conn_factory is not None:
        return source.conn_factory()
    from ..database import get_db_connection
    return get_db_connection()


def verify_platform_authority(conn, source, *, required_scope: str, lock: bool = False) -> PlatformAuthority:
    if isinstance(source, WandererServiceAuthority):
        from .wanderer_runtime_service import assert_turn_authority
        from .agent_actor_service import resolve_agent_actor
        from .wanderer_identity_service import current_life
        if required_scope not in {"platform:read", "platform:write"}:
            raise HTTPException(403, "散修运行授权不包含此能力。")
        try:
            wanderer = assert_turn_authority(conn, wanderer_id=source.wanderer_id,
                turn_id=source.turn_id, life_epoch=source.life_epoch, fencing_token=source.fencing_token,
                authority_version=source.authority_version)
        except (ValueError, LookupError, PermissionError) as exc:
            raise HTTPException(409, "散修运行身份或租约已失效。") from exc
        if str(wanderer.get("status")) != "active" or current_life(wanderer).get("expired"):
            raise HTTPException(403, "散修已暂停、陨落或寿元耗尽。")
        actor = resolve_agent_actor(conn, "student", int(wanderer["student_id"]), service_wanderer=wanderer)
        return PlatformAuthority(actor, "wanderer_service", None, None, source.fencing_token,
            None, None, 0, source.wanderer_id, str(source.turn_id), source.life_epoch, actor.authority_version)
    from .agent_delegation_service import verify_task_delegation
    grant = verify_task_delegation(conn, source, purpose="tools", required_scope=required_scope, lock_task=lock)
    if not grant.delegation.get("source_session_hash"):
        raise HTTPException(403, "该平台请求能力要求当前登录会话授权。")
    return PlatformAuthority(grant.actor, "human_delegation", int(grant.task["id"]),
        grant.attempt["id"], int(grant.attempt["fencing_token"]), grant.delegation["id"],
        grant.delegation["source_session_hash"], int(grant.delegation["created_at"]), original_grant=grant)
