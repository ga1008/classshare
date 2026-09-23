"""Field-level preferences endpoints, separate from complete profile updates."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ..db.connection import get_db_connection
from ..dependencies import get_current_preference_user
from ..services.user_ui_preferences_service import (
    MAX_PREFERENCE_VERSION,
    PreferenceConflict,
    get_ui_preferences,
    preference_context_token,
    update_ui_preferences,
    validate_preference_changes,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_NO_STORE = {"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization"}


class UIPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    palette_key: str | None = Field(default=None, min_length=1, max_length=20)
    appearance: str | None = Field(default=None, min_length=1, max_length=20)
    glass: str | None = Field(default=None, min_length=1, max_length=20)
    backdrop: str | None = Field(default=None, min_length=1, max_length=136)
    backdrop_color: str | None = Field(default=None, min_length=1, max_length=20)
    version: StrictInt = Field(ge=0, le=MAX_PREFERENCE_VERSION)

    def changes(self) -> dict[str, str]:
        return validate_preference_changes(self.model_dump(exclude_unset=True, exclude={"version"}))

    @model_validator(mode="after")
    def validate_changes(self):
        self.changes()
        return self


@router.get("/api/profile/ui-preferences")
def read_preferences(user: dict = Depends(get_current_preference_user)):
    try:
        with get_db_connection() as conn:
            preferences = get_ui_preferences(conn, user)
    except Exception as exc:
        logger.exception("Unable to read account UI preferences")
        raise HTTPException(503, "界面偏好同步暂不可用，请稍后重试。", headers=_NO_STORE) from exc
    return JSONResponse({"preferences": preferences}, headers=_NO_STORE)


@router.patch("/api/profile/ui-preferences")
def save_preferences(payload: UIPreferencesUpdate, request: Request, user: dict = Depends(get_current_preference_user)):
    # Cookie identity can change while another tab still displays the old SSR.
    supplied_context = request.headers.get("X-UI-Preferences-Context", "")
    if not supplied_context.isascii() or not hmac.compare_digest(supplied_context, preference_context_token(user)):
        raise HTTPException(409, detail={"code": "identity_changed", "message": "登录账号已变化，请刷新页面后再选择界面偏好。"}, headers=_NO_STORE)
    try:
        with get_db_connection() as conn:
            preferences = update_ui_preferences(conn, user, changes=payload.changes(), version=payload.version)
    except PreferenceConflict as exc:
        return JSONResponse({"code": "version_conflict", "message": str(exc), "preferences": exc.current}, status_code=409, headers=_NO_STORE)
    except Exception as exc:
        logger.exception("Unable to update account UI preferences")
        raise HTTPException(503, "界面偏好未同步，请重新选择以重试。", headers=_NO_STORE) from exc
    return JSONResponse({"preferences": preferences}, headers=_NO_STORE)
