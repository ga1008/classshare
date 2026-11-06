"""Synthetic social fixtures and real HTTP write flows for the capacity probe."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
from urllib.parse import quote
import uuid


def seed_social(args, runtime: Path, fixture: dict) -> dict:
    """Call only after platform_capacity_probe.establish_boundary."""
    expected = f"postgresql://rehearsal_admin@127.0.0.1:{args.pg_port}/lanshare_assessment_rehearsal_capacity"
    if (fixture.get("uiV3Synthetic") is not True or os.environ.get("DATABASE_URL") != expected
            or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"):
        raise RuntimeError("Social seeding requires the explicit native synthetic boundary")
    return _seed_validated_social(runtime, fixture, users=args.users)


def _seed_validated_social(runtime: Path, fixture: dict, *, users: int) -> dict:
    """Shared seed body, after native or Linux controller identity validation."""
    from classroom_app.database import get_db_connection
    from classroom_app.services import social_friend_service as friends, social_group_service as groups
    from classroom_app.services.learning_progress_service import LEARNING_LEVELS
    from classroom_app.services.social_common import dm_key
    from classroom_app.services.social_relations import are_friends

    teacher = {"id": int(fixture["teacher"]["id"]), "role": "teacher"}
    teacher_identity = f"teacher:{teacher['id']}"
    identifiers = [f"CAPACITY-SYNTHETIC-{i:04d}" for i in range(users)]
    level = next(level for level in LEARNING_LEVELS if int(level["tier"]) == 1)
    with get_db_connection() as conn:
        row = conn.execute("SELECT email FROM teachers WHERE id = ?", (teacher["id"],)).fetchone()
        if not row or row["email"] != fixture["teacher"]["email"] or not row["email"].endswith("@example.invalid"):
            raise RuntimeError("Social fixture teacher is not the owned synthetic teacher")
        conn.execute("UPDATE teachers SET school_code = 'capacity.synthetic' WHERE id = ?", (teacher["id"],))
        rows = conn.execute("SELECT id,student_id_number,class_id,description FROM students "
                            "WHERE student_id_number LIKE 'CAPACITY-SYNTHETIC-%' ORDER BY student_id_number").fetchall()
        students = {str(row["student_id_number"]): int(row["id"]) for row in rows
                    if int(row["class_id"]) == int(fixture["classId"])
                    and row["description"] == "owned synthetic capacity fixture"}
        if any(identifier not in students for identifier in identifiers):
            raise RuntimeError("Expected synthetic students missing")
        for identifier in identifiers:
            student_id = students[identifier]
            conn.execute("UPDATE students SET school_code = 'capacity.synthetic' WHERE id = ?", (student_id,))
            # Explicit fixture entitlement. Runtime friendship gates remain active.
            conn.execute("""INSERT INTO learning_certificates (
                class_offering_id,student_id,stage_key,level_key,level_name,tier,title,certificate_code,metadata_json
                ) VALUES (?,?,?,?,?,1,?,?,?) ON CONFLICT DO NOTHING""",
                (fixture["classOfferingId"], student_id, level["key"], level["key"], level["name"],
                 level["certificate_title"], f"CAPACITY-SYNTHETIC-SOCIAL-{student_id}",
                 json.dumps({"synthetic_capacity_entitlement": True})))
        conn.commit()
    # Every student sends only one request, and the teacher answers it. This uses
    # the real scope, per-person daily cap, friendship and notification services.
    for identifier in identifiers:
        student_id = students[identifier]
        student = {"id": student_id, "role": "student"}
        identity = f"student:{student_id}"
        with get_db_connection() as conn:
            if not are_friends(conn, teacher_identity, identity):
                request = friends.send_request(conn, student, teacher_identity, "合成容量测试好友申请")
                friends.accept_request(conn, teacher, int(request["request_id"]))
            conn.commit()
    group_students = identifiers[:199]  # Existing 200-person limit includes its owner.
    with get_db_connection() as conn:
        existing = conn.execute("SELECT id FROM social_groups WHERE owner_identity = ? AND name = ? "
                                "AND dissolved_at IS NULL ORDER BY id LIMIT 1",
                                (teacher_identity, "合成容量测试群")).fetchone()
        if existing:
            group_id = int(existing["id"])
        else:
            group = groups.create_group(conn, teacher, "合成容量测试群", [f"student:{students[item]}" for item in group_students])
            group_id = int(group["id"])
        detail = groups.group_detail(conn, teacher, group_id)
        expected_members = {teacher_identity, *(f"student:{students[item]}" for item in group_students)}
        if {person["identity"] for person in detail["members"]} != expected_members:
            raise RuntimeError("Synthetic group membership differs from its declared capacity fixture")
        conn.commit()
    metadata = {"synthetic": True, "teacher_id": teacher["id"], "group_id": group_id,
                "group_member_count": len(expected_members), "group_student_count": len(group_students),
                "identifiers": identifiers, "student_ids": [students[item] for item in identifiers],
                "dm_keys": [dm_key(teacher_identity, f"student:{students[item]}") for item in identifiers],
                "nonce_prefix": f"capacity-{uuid.uuid4().hex[:12]}",
                "fixture_entitlements": "tier 1 certificates and same synthetic school"}
    (runtime / "capacity-social.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def load_social(runtime: Path) -> dict:
    metadata = json.loads((runtime / "capacity-social.json").read_text(encoding="utf-8"))
    if metadata.get("synthetic") is not True:
        raise RuntimeError("Missing synthetic social fixture")
    metadata["nonce_prefix"] = f"capacity-{uuid.uuid4().hex[:12]}"
    return metadata


async def social_action(client, token: str, identity_index: int, sequence: int, metadata: dict, record, *, kind="group") -> None:
    """Send, verify same-nonce idempotency, read exact message and advance read cursor."""
    if metadata.get("synthetic") is not True:
        raise RuntimeError("Missing synthetic social fixture")
    identity_index %= len(metadata["student_ids"])
    read_only = kind == "read"
    member = identity_index < int(metadata["group_student_count"])
    if not member:
        kind = "dm"
    key = metadata["dm_keys"][identity_index] if kind == "dm" else f"group:{metadata['group_id']}"
    path = f"/api/social/conversations/{quote(key, safe='')}/messages"
    headers = {"Cookie": f"access_token={token}"}
    label = "social-dm" if kind == "dm" else "social-group"

    async def call(method, route, suffix, **kwargs):
        began = time.perf_counter()
        response = await client.request(method, route, headers=headers, **kwargs)
        record(f"{label}-{suffix}", began, response)
        if response.status_code != 200:
            raise AssertionError(f"{label}-{suffix} HTTP {response.status_code}")
        return response.json()

    if read_only:
        if member:
            await call("GET", f"/api/social/groups/{metadata['group_id']}", "detail")
        await call("GET", path, "read")
        return
    nonce = f"{metadata['nonce_prefix']}-{kind}-{sequence}"
    body = {"body": f"合成容量消息 {sequence}", "body_format": "text", "client_nonce": nonce}
    first, retry = await asyncio.gather(call("POST", path, "send", json=body), call("POST", path, "retry", json=body))
    message_id = int(first.get("id") or 0)
    if message_id <= 0 or message_id != int(retry.get("id") or 0):
        raise AssertionError("Same-nonce send did not return one durable message")
    expected_identity = f"student:{metadata['student_ids'][identity_index]}"
    if first.get("sender", {}).get("identity") != expected_identity or first.get("conversation_key") != key:
        raise AssertionError("Social send receipt belongs to another actor or conversation")
    history = await call("GET", path, "read", params={"before_id": message_id + 1, "limit": 1})
    if [int(item["id"]) for item in history.get("items", [])] != [message_id]:
        raise AssertionError("Durable social message missing from its authorized history")
    read = await call("POST", path.removesuffix("/messages") + "/read", "mark-read", json={"last_message_id": message_id})
    if int(read.get("last_message_id") or 0) < message_id:
        raise AssertionError("Read cursor failed to cover the durable message")


async def verify_group_exclusion(client, token: str, metadata: dict) -> None:
    """A valid user beyond the group's 200-member capacity must receive 403."""
    response = await client.get(f"/api/social/groups/{metadata['group_id']}",
                                headers={"Cookie": f"access_token={token}"})
    if response.status_code != 403:
        raise AssertionError(f"Synthetic nonmember group read returned {response.status_code}")
