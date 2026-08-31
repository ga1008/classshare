"""微信小程序端 JSON API（/api/mp/*）.

Thin JSON wrappers over existing services for the uni-app mini program.
Auth is bearer-token based (``mp_sessions``), not cookie based — see
``services.wechat_mp_service`` for the session model rationale.
"""

from fastapi import APIRouter

from . import auth as _auth
from . import home as _home
from . import life_tips as _life_tips
from . import tasks as _tasks
from . import teacher as _teacher
from . import todos as _todos

router = APIRouter(prefix="/api/mp")
router.include_router(_auth.router)
router.include_router(_home.router)
router.include_router(_life_tips.router)
router.include_router(_tasks.router)
router.include_router(_teacher.router)
router.include_router(_todos.router)
