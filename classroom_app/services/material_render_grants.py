"""A sandboxed frame cannot send the platform's SameSite session cookie.

The path grant only authorizes reads beneath one renderer node. It is tied to
the live web session and never doubles as a platform authentication token.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest

from jose import JWTError, jwt

from ..config import ALGORITHM, SECRET_KEY
from ..db.sessions import get_user_session
from ..dependencies import build_session_user_key

_AUDIENCE = "lanshare-material-render"
_TOPIC_AUDIENCE = "lanshare-project-topic-render"


def issue_material_render_grant(material_id: int, user: dict) -> str | None:
    session_id = user.get("session_id")
    if not session_id or user.get("role") not in {"teacher", "student"}:
        return None
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "aud": _AUDIENCE,
            "exp": now + timedelta(hours=4),
            "mid": material_id,
            "uid": int(user["id"]),
            "role": user["role"],
            "sid": session_id,
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def resolve_material_render_grant(grant: str) -> tuple[int, dict] | None:
    try:
        data = jwt.decode(
            grant, SECRET_KEY, algorithms=[ALGORITHM], audience=_AUDIENCE,
            options={"require_aud": True, "require_exp": True},
        )
        material_id, user_id, role = int(data["mid"]), int(data["uid"]), data["role"]
    except (JWTError, KeyError, TypeError, ValueError):
        return None
    if material_id <= 0 or user_id <= 0 or role not in {"teacher", "student"}:
        return None
    session = get_user_session(build_session_user_key(str(user_id), role))
    if not session or not data.get("sid") or session["session_id"] != data["sid"]:
        return None
    return material_id, {"id": user_id, "role": role}


def issue_topic_render_grant(user: dict, scope: dict) -> str | None:
    session_id = user.get("session_id")
    if not session_id or user.get("role") not in {"teacher", "student"}:
        return None
    return jwt.encode({
        "aud": _TOPIC_AUDIENCE, "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        "uid": int(user["id"]), "role": user["role"], "scope": scope,
        "session_digest": sha256(str(session_id).encode()).hexdigest(),
    }, SECRET_KEY, algorithm=ALGORITHM)


def resolve_topic_render_grant(grant: str) -> tuple[dict, dict] | None:
    try:
        data = jwt.decode(grant, SECRET_KEY, algorithms=[ALGORITHM], audience=_TOPIC_AUDIENCE,
                          options={"require_aud": True, "require_exp": True})
        user_id, role, scope = int(data["uid"]), data["role"], data["scope"]
    except (JWTError, TypeError, ValueError, KeyError):
        return None
    if role not in {"teacher", "student"} or user_id < 1 or not isinstance(scope, dict):
        return None
    session = get_user_session(build_session_user_key(str(user_id), role))
    if not session or not session["session_id"]:
        return None
    digest = sha256(str(session["session_id"]).encode()).hexdigest()
    if not compare_digest(digest, str(data.get("session_digest") or "")):
        return None
    return scope, {"id": user_id, "role": role}
