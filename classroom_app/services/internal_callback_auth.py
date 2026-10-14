"""HMAC signing for the AI assistant -> main app grading callback.

The callback writes scores, so it must only accept requests from our own AI
worker. Both containers share ``SECRET_KEY`` (docker.env); the signing key is
derived from it unless ``AI_CALLBACK_SECRET`` is set explicitly. The signature
covers a timestamp plus the exact request bytes, and stale timestamps are
rejected to stop replays of captured callbacks.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Mapping

from dotenv import load_dotenv
from fastapi import HTTPException

# Both the web app and the AI worker load .env; standalone helpers (the QA
# mock assistant) must resolve the same SECRET_KEY. Never overrides real env.
load_dotenv()

TIMESTAMP_HEADER = "X-LanShare-Callback-Timestamp"
SIGNATURE_HEADER = "X-LanShare-Callback-Signature"
MAX_CLOCK_SKEW_SECONDS = 600
_KEY_CONTEXT = b"lanshare-ai-grading-callback-v1"


def _signing_key() -> bytes:
    explicit = str(os.getenv("AI_CALLBACK_SECRET") or "").strip()
    if explicit:
        return explicit.encode("utf-8")
    base = str(os.getenv("SECRET_KEY") or "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME").encode("utf-8")
    return hmac.new(base, _KEY_CONTEXT, hashlib.sha256).digest()


def _signature(body: bytes, timestamp: str) -> str:
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def signed_callback_request(payload: Mapping[str, Any], *, now: float | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    timestamp = str(int(now if now is not None else time.time()))
    return body, {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: _signature(body, timestamp),
    }


def verify_signed_callback(body: bytes, timestamp: str | None, signature: str | None, *, now: float | None = None) -> None:
    timestamp = str(timestamp or "").strip()
    signature = str(signature or "").strip()
    if not timestamp or not signature or not (timestamp.isascii() and timestamp.isdigit()):
        raise HTTPException(401, "callback signature required")
    current = now if now is not None else time.time()
    if abs(current - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
        raise HTTPException(401, "callback signature expired")
    if not hmac.compare_digest(_signature(body, timestamp), signature):
        raise HTTPException(401, "callback signature invalid")
