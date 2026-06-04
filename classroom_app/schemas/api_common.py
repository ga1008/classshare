from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiErrorCode(str, Enum):
    BAD_REQUEST = "bad_request"
    LOGIN_REQUIRED = "login_required"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    SERVICE_UNAVAILABLE = "service_unavailable"
    UPSTREAM_ERROR = "upstream_error"
    INTERNAL_ERROR = "internal_error"
    UNKNOWN_ERROR = "unknown_error"


API_ERROR_CODE_BY_STATUS: dict[int, ApiErrorCode] = {
    400: ApiErrorCode.BAD_REQUEST,
    401: ApiErrorCode.LOGIN_REQUIRED,
    403: ApiErrorCode.PERMISSION_DENIED,
    404: ApiErrorCode.NOT_FOUND,
    409: ApiErrorCode.CONFLICT,
    413: ApiErrorCode.PAYLOAD_TOO_LARGE,
    415: ApiErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ApiErrorCode.VALIDATION_ERROR,
    429: ApiErrorCode.RATE_LIMITED,
    500: ApiErrorCode.INTERNAL_ERROR,
    502: ApiErrorCode.UPSTREAM_ERROR,
    503: ApiErrorCode.SERVICE_UNAVAILABLE,
    504: ApiErrorCode.UPSTREAM_ERROR,
}


class ApiFlexibleRecord(BaseModel):
    model_config = ConfigDict(extra="allow")


class ApiStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


class ApiSuccessResponse(ApiStatusResponse):
    status: str = "success"


class ApiErrorBody(ApiFlexibleRecord):
    code: ApiErrorCode
    message: str = Field(min_length=1)
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ApiErrorResponse(ApiFlexibleRecord):
    detail: str | dict[str, Any] | list[Any] | None = None
    code: ApiErrorCode
    error: ApiErrorBody


def api_error_code_for_status(status_code: int) -> ApiErrorCode:
    return API_ERROR_CODE_BY_STATUS.get(int(status_code), ApiErrorCode.UNKNOWN_ERROR)


def error_message_from_detail(detail: Any, fallback: str) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        for key in ("message", "error", "reason", "detail"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def build_error_payload(
    *,
    detail: str | dict[str, Any] | list[Any] | None,
    message: str,
    code: ApiErrorCode,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    legacy_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code.value,
        "message": message,
    }
    if details:
        error["details"] = details
    if request_id:
        error["request_id"] = request_id

    payload: dict[str, Any] = {
        "detail": detail,
        "code": code.value,
        "error": error,
    }
    if legacy_fields:
        payload.update(legacy_fields)
    return payload

