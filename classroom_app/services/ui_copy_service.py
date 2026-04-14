from __future__ import annotations

import copy
from typing import Any

STATIC_UI_COPY_SNAPSHOT: dict[str, dict[str, dict[str, str]]] = {
    "dashboard": {
        "teacher": {
            "hero_eyebrow": "老师的小指挥台",
            "hero_title": "今天也把课堂带得稳稳的",
            "hero_subtitle": "{{name}}，常用入口和课堂提醒都给你摆好啦，先挑一件顺手的，我们慢慢把节奏带起来。",
            "spotlight_pending_label": "眼前最要紧",
            "spotlight_pending_note": "先把待批改收一收，后面的课堂推进会轻快很多。",
            "spotlight_reset_label": "等你点头",
            "spotlight_reset_note": "有同学在等你审核找回密码，忙完记得去看一眼。",
            "spotlight_unread_label": "新消息敲门",
            "spotlight_unread_note": "消息中心来了新动静，回头看看，别让提醒扑空。",
            "spotlight_login_label": "今日到课",
            "spotlight_login_note": "今天的登录情况都乖乖记在这里了。",
            "quick_actions_title": "顺手入口",
            "quick_actions_subtitle": "常用功能放近一点，备课、发布、查看都能少绕两步。",
            "focus_title": "先忙这几件",
            "focus_subtitle": "把最影响课堂节奏的事先处理掉，后面就会顺很多。",
            "focus_empty_title": "今天先喘口气",
            "focus_empty_description": "眼下没有急事，正适合补材料、修试卷，或者慢慢看看课堂反馈。",
            "activity_title": "最近有点热闹",
            "activity_subtitle": "课堂里刚发生的事都在这儿，随手翻一眼就能接上。",
            "action_offering_label": "开一间课堂",
            "action_offering_description": "把班级和课程牵上线，新课堂很快就能开张。",
            "action_materials_label": "整理材料",
            "action_materials_description": "把课件和文档收整齐，后面分发会省心很多。",
            "action_exams_label": "看看题库",
            "action_exams_description": "试卷、考试和题目都在这里慢慢打磨。",
            "action_system_label": "处理审核",
            "action_system_description": "申请、安全记录和系统小提醒，都在这里照看。",
            "empty_title": "先把第一间课堂点亮吧",
            "empty_description": "现在还没有开设中的课堂，先把班级和课程连起来，后面布置任务会顺手很多。",
            "empty_action_label": "去开设课堂",
        },
        "student": {
            "hero_eyebrow": "今天的学习小抄",
            "hero_title": "先看看今天从哪一格开始",
            "hero_subtitle": "{{name}}，课程、待办和提醒都帮你收在这里啦，一眼就能找到接下来要做什么。",
            "spotlight_pending_label": "先做这件",
            "spotlight_pending_note": "把最近要交的作业或考试先收掉，心里会轻松很多。",
            "spotlight_unread_label": "有新提醒啦",
            "spotlight_unread_note": "消息中心有新反馈，顺手看看，别让重要信息悄悄溜走。",
            "spotlight_login_label": "常来坐坐",
            "spotlight_login_note": "常回来看看，进度、提醒和安全记录都会接得更稳。",
            "quick_actions_title": "抄近路入口",
            "quick_actions_subtitle": "常用入口放在手边，少点几下，也能少分一点神。",
            "focus_title": "先顾这几件",
            "focus_subtitle": "把眼下最要紧的事情处理掉，后面的学习节奏会舒服很多。",
            "activity_title": "最近的小动静",
            "activity_subtitle": "老师反馈、课堂互动和提醒消息，都在这里排好队等你看。",
            "priority_unread_title": "消息中心有新内容",
            "priority_unread_description": "可能有老师反馈、批改结果，或者同学在找你接话。",
            "priority_empty_title": "今天的节奏还不错",
            "priority_empty_description": "眼下没有急事，去翻翻资料、看看讨论，或者顺手复盘一下都很值。",
            "action_priority_label": "直奔重点",
            "action_priority_description": "把你现在最该先处理的那件事直接拎出来。",
            "action_message_label": "去看消息",
            "action_message_description": "私信、提醒和批改反馈都在这里乖乖排着队。",
            "action_security_label": "改个密码",
            "action_security_description": "顺手把账号也照顾好，心里更踏实一点。",
            "empty_title": "这会儿还没有可进入的课堂",
            "empty_description": "等老师给你的班级开课后，入口就会出现在这里啦。",
            "empty_action_label": "先去消息中心",
        },
    },
    "classroom": {
        "teacher": {
            "hero_eyebrow": "老师的课堂小窝",
            "hero_lead": "{{name}}，这间课堂的任务、材料、资源和讨论都给你收整好了，推进起来会顺很多。",
            "assignment_title": "作业与考试",
            "assignment_subtitle": "发布、调整、回看进度都在这里，课堂节奏不用来回找。",
            "assignment_empty_title": "这门课还没发新任务",
            "assignment_empty_description": "可以先新建作业，或者从试卷库挑一份考试慢慢放进来。",
            "materials_title": "课程材料",
            "materials_subtitle": "学生要看的课程文档都在这里，整理好以后复用和分发都很省心。",
            "resources_title": "软件分享与课堂资源",
            "resources_subtitle": "课件、工具和示例资料放在一起，发给学生时会利落很多。",
            "discussion_title": "即时讨论",
            "discussion_subtitle": "这儿适合抛问题、接反馈，也适合把灵光一闪先放进来。",
            "discussion_detail_template": "想提醒一句、追问一句，或者轻轻接个梗，都能把课堂气氛带起来。",
            "spotlight_draft_label": "还差临门一脚",
            "spotlight_draft_note": "还有任务停在草稿里，补完就能发给学生啦。",
            "spotlight_active_label": "课堂正在热机",
            "spotlight_active_note": "任务已经跑起来了，现在很适合继续补材料和打磨体验。",
        },
        "student": {
            "hero_eyebrow": "欢迎回到这间课堂",
            "hero_lead": "{{name}}，任务、材料、资源和讨论都在这里，学到哪儿就从哪儿接着往下走。",
            "assignment_title": "我的作业与考试",
            "assignment_subtitle": "要求、入口和提交状态都在这儿，先看清要求，再稳稳交上去。",
            "assignment_empty_title": "这门课暂时还没有新任务",
            "assignment_empty_description": "老师一发布作业或考试，这里就会第一时间把入口摆出来。",
            "materials_title": "课程材料",
            "materials_subtitle": "老师分配的文档都在这里，查资料、读 README、下载复习都很方便。",
            "resources_title": "软件分享与课堂资源",
            "resources_subtitle": "课件、工具和实验资料放在一起，需要什么就直接来拿。",
            "discussion_title": "即时讨论",
            "discussion_subtitle": "卡住了就问，想到就聊，不用太端着，先把话抛出来最重要。",
            "discussion_detail_template": "一句问题、一点心得、一个新发现，都可能把讨论慢慢聊热。",
            "spotlight_pending_label": "还差这几项",
            "spotlight_pending_note": "先把还没提交的任务收掉，后面的学习节奏会轻松很多。",
            "spotlight_submitted_label": "已经交上去啦",
            "spotlight_submitted_note": "你已经有内容在提交流程里了，记得回来看看老师的反馈。",
            "spotlight_empty_label": "先看看四周",
            "spotlight_empty_note": "老师一发新任务，这里会第一时间提醒你，不用担心错过。",
        },
    },
}


def get_ui_copy_block(
    conn,
    *,
    scene: str,
    role: str,
) -> dict[str, Any]:
    del conn
    normalized_scene = str(scene or "").strip().lower()
    normalized_role = "teacher" if str(role or "").strip().lower() == "teacher" else "student"
    return copy.deepcopy(
        STATIC_UI_COPY_SNAPSHOT.get(normalized_scene, {}).get(normalized_role, {})
    )


def render_ui_copy_block(block: dict[str, Any], tokens: dict[str, Any] | None = None) -> dict[str, Any]:
    return _render_copy_tokens(copy.deepcopy(block), tokens or {})


async def ensure_ui_copy_snapshot(*, reason: str = "startup", force: bool = False) -> str:
    del reason, force
    return "builtin"


def start_ui_copy_refresh_scheduler() -> None:
    return None


async def stop_ui_copy_refresh_scheduler() -> None:
    return None


def _render_copy_tokens(value: Any, tokens: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_copy_tokens(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_copy_tokens(item, tokens) for item in value]
    if not isinstance(value, str):
        return value

    rendered = value
    for key, token_value in tokens.items():
        normalized = "" if token_value is None else str(token_value)
        rendered = rendered.replace(f"{{{{{key}}}}}", normalized)
    return rendered
