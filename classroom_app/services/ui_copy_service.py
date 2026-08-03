from __future__ import annotations

import copy
from typing import Any

STATIC_UI_COPY_SNAPSHOT: dict[str, dict[str, dict[str, str]]] = {
    "dashboard": {
        "teacher": {
            "hero_eyebrow": "老师的小指挥台",
            "hero_title": "今天也把课堂带得稳稳的",
            "hero_subtitle": "{{name}}，常用入口和课堂提醒都在这里。",
            "spotlight_pending_label": "眼前最要紧",
            "spotlight_pending_note": "先把待批改收掉，课堂推进更轻快。",
            "spotlight_reset_label": "等你点头",
            "spotlight_reset_note": "有同学在等你审核找回密码。",
            "spotlight_unread_label": "新消息敲门",
            "spotlight_unread_note": "消息中心有新动静。",
            "spotlight_login_label": "今日到课",
            "spotlight_login_note": "今天的登录情况记录在这里。",
            "quick_actions_title": "快捷入口",
            "quick_actions_subtitle": "开课、材料、题库与审核，一步直达。",
            "focus_title": "优先处理",
            "focus_subtitle": "最要紧的事，先在这里收掉。",
            "focus_empty_title": "暂无紧急事项",
            "focus_empty_description": "可以趁现在补充材料或修订试卷。",
            "activity_title": "最近动态",
            "activity_subtitle": "课堂里刚发生的提交、反馈与提醒。",
            "action_offering_label": "开一间课堂",
            "action_offering_description": "把班级和课程连起来。",
            "action_materials_label": "整理材料",
            "action_materials_description": "课件和文档收整齐，分发更省心。",
            "action_exams_label": "看看题库",
            "action_exams_description": "试卷、考试和题目都在这里。",
            "action_system_label": "处理审核",
            "action_system_description": "申请、安全记录和系统提醒。",
            "empty_title": "先把第一间课堂点亮吧",
            "empty_description": "先把班级和课程连起来，就能开出第一间课堂。",
            "empty_action_label": "去开设课堂",
        },
        "student": {
            "hero_eyebrow": "今天的学习小抄",
            "hero_title": "先看看今天从哪一格开始",
            "hero_subtitle": "{{name}}，课程、待办和提醒都在这里。",
            "spotlight_pending_label": "先做这件",
            "spotlight_pending_note": "先把最近要交的作业或考试收掉。",
            "spotlight_unread_label": "有新提醒啦",
            "spotlight_unread_note": "消息中心有新反馈，顺手看看。",
            "spotlight_login_label": "累计登录",
            "spotlight_login_note": "进度、提醒和安全记录都在这里。",
            "quick_actions_title": "快捷入口",
            "quick_actions_subtitle": "重点任务、错题、成绩与成就，一步直达。",
            "focus_title": "优先处理",
            "focus_subtitle": "最要紧的事，先在这里收掉。",
            "activity_title": "最近动态",
            "activity_subtitle": "老师反馈、课堂互动和提醒消息。",
            "priority_unread_title": "消息中心有新内容",
            "priority_unread_description": "可能有老师反馈、批改结果或同学消息。",
            "priority_empty_title": "今天的节奏还不错",
            "priority_empty_description": "眼下没有急事，可以翻翻资料或复盘错题。",
            "action_priority_label": "直奔重点",
            "action_priority_description": "直达当前最该处理的事。",
            "action_message_label": "去看消息",
            "action_message_description": "私信、提醒和批改反馈。",
            "action_security_label": "改个密码",
            "action_security_description": "更新密码，保护账号。",
            "empty_title": "这会儿还没有可进入的课堂",
            "empty_description": "老师开课后，入口会出现在这里。",
            "empty_action_label": "先去消息中心",
            "empty_step_profile_title": "确认个人信息",
            "empty_step_profile_description": "确认姓名、班级和联系方式，方便对上名单。",
            "empty_step_profile_label": "去个人中心",
            "empty_step_classroom_title": "等待任课教师开课",
            "empty_step_classroom_description": "老师开课后，入口自动出现在首页。",
            "empty_step_message_title": "看看通知",
            "empty_step_message_description": "开课、作业和反馈提醒都会进入消息中心。",
            "empty_step_message_label": "去消息中心",
        },
    },
    "classroom": {
        "teacher": {
            "hero_eyebrow": "老师的课堂小窝",
            "hero_lead": "{{name}}，任务、材料、资源和讨论都在这里。",
            "assignment_title": "作业与考试",
            "assignment_subtitle": "发布、调整与回看进度。",
            "assignment_empty_title": "这门课还没发新任务",
            "assignment_empty_description": "可以新建作业，或从试卷库发布考试。",
            "materials_title": "课程材料",
            "materials_subtitle": "学生要看的课程文档都在这里。",
            "resources_title": "软件分享与课堂资源",
            "resources_subtitle": "课件、工具和示例资料。",
            "discussion_title": "即时讨论",
            "discussion_subtitle": "抛问题、接反馈的地方。",
            "discussion_detail_template": "提醒、追问或闲聊，都能带起课堂气氛。",
            "spotlight_draft_label": "还差临门一脚",
            "spotlight_draft_note": "还有任务停在草稿里，补完即可发布。",
            "spotlight_active_label": "课堂正在热机",
            "spotlight_active_note": "任务已经跑起来了，可以继续补材料。",
        },
        "student": {
            "hero_eyebrow": "欢迎回到这间课堂",
            "hero_lead": "{{name}}，任务、材料、资源和讨论都在这里。",
            "assignment_title": "我的作业与考试",
            "assignment_subtitle": "看清要求，按时提交。",
            "assignment_empty_title": "这门课暂时还没有新任务",
            "assignment_empty_description": "老师发布任务后，入口会出现在这里。",
            "materials_title": "课程材料",
            "materials_subtitle": "老师分配的课程文档都在这里。",
            "resources_title": "软件分享与课堂资源",
            "resources_subtitle": "课件、工具和实验资料。",
            "discussion_title": "即时讨论",
            "discussion_subtitle": "卡住了就问，想到就聊。",
            "discussion_detail_template": "一句问题或一点心得，都能把讨论聊热。",
            "spotlight_pending_label": "还差这几项",
            "spotlight_pending_note": "先把还没提交的任务收掉。",
            "spotlight_submitted_label": "已经交上去啦",
            "spotlight_submitted_note": "已有提交在流程里，记得回看老师反馈。",
            "spotlight_empty_label": "先看看四周",
            "spotlight_empty_note": "老师发新任务时，这里会第一时间提醒。",
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
