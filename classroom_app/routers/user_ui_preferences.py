"""Field-level preferences endpoints, separate from complete profile updates."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..db.connection import get_db_connection
from ..dependencies import get_current_student
from ..services.user_ui_preferences_service import (
    PALETTE_KEYS,
    PreferenceConflict,
    get_ui_preferences,
    preference_context_token,
    update_ui_preferences,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_NO_STORE = {"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization"}


class UIPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    palette_key: str = Field(min_length=1, max_length=20)
    version: StrictInt = Field(ge=0, le=2147483646)


@router.get("/api/profile/ui-preferences")
def read_preferences(user: dict = Depends(get_current_student)):
    try:
        with get_db_connection() as conn:
            preferences = get_ui_preferences(conn, user)
    except Exception as exc:
        logger.exception("Unable to read account UI preferences")
        raise HTTPException(503, "配色同步暂不可用，请稍后重试。", headers=_NO_STORE) from exc
    return JSONResponse({"preferences": preferences}, headers=_NO_STORE)


@router.patch("/api/profile/ui-preferences")
def save_preferences(payload: UIPreferencesUpdate, request: Request, user: dict = Depends(get_current_student)):
    # Cookie identity can change while another tab still displays the old SSR.
    supplied_context = request.headers.get("X-UI-Preferences-Context", "")
    if not hmac.compare_digest(supplied_context, preference_context_token(user)):
        raise HTTPException(409, detail={"code": "identity_changed", "message": "登录账号已变化，请刷新页面后再选择配色。"}, headers=_NO_STORE)
    if payload.palette_key not in PALETTE_KEYS:
        raise HTTPException(422, "请选择提供的界面配色。", headers=_NO_STORE)
    try:
        with get_db_connection() as conn:
            preferences = update_ui_preferences(conn, user, palette_key=payload.palette_key, version=payload.version)
    except PreferenceConflict as exc:
        return JSONResponse({"code": "version_conflict", "message": str(exc), "preferences": exc.current}, status_code=409, headers=_NO_STORE)
    except Exception as exc:
        logger.exception("Unable to update account UI preferences")
        raise HTTPException(503, "配色未同步，请重新选择以重试。", headers=_NO_STORE) from exc
    return JSONResponse({"preferences": preferences}, headers=_NO_STORE)
