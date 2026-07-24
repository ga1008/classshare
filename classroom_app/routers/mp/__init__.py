"""微信小程序端 JSON API（/api/mp/*）.

Thin JSON wrappers over existing services for the uni-app mini program.
Auth is bearer-token based (``mp_sessions``), not cookie based — see
``services.wechat_mp_service`` for the session model rationale.
"""

from fastapi import APIRouter

from . import auth as _auth
from . import life_tips as _life_tips

router = APIRouter(prefix="/api/mp")
router.include_router(_auth.router)
router.include_router(_life_tips.router)
