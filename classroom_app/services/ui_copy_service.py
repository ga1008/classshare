from __future__ import annotations

import copy
from typing import Any

STATIC_UI_COPY_SNAPSHOT: dict[str, dict[str, dict[str, str]]] = {
    "dashboard": {
        "teacher": {
            "hero_eyebrow": "教师首页",
            "hero_title": "欢迎回来，今天的教学安排都在这里",
            "hero_subtitle": "{{name}}，常用入口、待办与课堂提醒已为你整理好。",
            "spotlight_pending_label": "待批改",
            "spotlight_pending_note": "优先处理已提交的作业与试卷，学生能更早看到反馈。",
            "spotlight_reset_label": "待审核申请",
            "spotlight_reset_note": "有同学提交了密码找回申请，等待你核实。",
            "spotlight_unread_label": "未读消息",
            "spotlight_unread_note": "消息中心有新的通知或私信。",
            "spotlight_login_label": "今日登录",
            "spotlight_login_note": "最近登录与账号状态记录在这里。",
            "quick_actions_title": "常用入口",
            "quick_actions_subtitle": "开课、资料、试卷与审核，直接进入。",
            "focus_title": "优先处理",
            "focus_subtitle": "眼下最需要推进的事项排在最前面。",
            "focus_empty_title": "目前没有待处理的紧急事项",
            "focus_empty_description": "可以趁空整理课程材料，或修订接下来的试卷与任务。",
            "activity_title": "近期动态",
            "activity_subtitle": "课堂里刚发生的提交、反馈与提醒。",
            "action_offering_label": "开设课堂",
            "action_offering_description": "为班级关联课程与教材，开启新课堂。",
            "action_materials_label": "课程资料",
            "action_materials_description": "集中整理课件、学习文档与参考材料。",
            "action_exams_label": "试卷库",
            "action_exams_description": "管理试卷、题目与考试分配。",
            "action_system_label": "审核与找回",
            "action_system_description": "处理学生找回申请与账号安全提醒。",
            "empty_title": "还没有开设课堂",
            "empty_description": "先关联班级和课程，就可以开始这一学期的教学。",
            "empty_action_label": "去开设课堂",
        },
        "student": {
            "hero_eyebrow": "学生首页",
            "hero_title": "今天先从最重要的一件事开始",
            "hero_subtitle": "{{name}}，你的课程、待办任务和提醒都在这里。",
            "spotlight_pending_label": "待完成任务",
            "spotlight_pending_note": "先处理临近截止的作业或考试，心里更踏实。",
            "spotlight_unread_label": "未读消息",
            "spotlight_unread_note": "消息中心有新的老师反馈或通知，记得查看。",
            "spotlight_login_label": "累计登录",
            "spotlight_login_note": "学习进度、提醒与账号安全记录都在这里。",
            "quick_actions_title": "常用入口",
            "quick_actions_subtitle": "任务、错题复盘、成绩单与成长记录，直接进入。",
            "focus_title": "优先处理",
            "focus_subtitle": "临近截止和未读反馈排在最前面。",
            "activity_title": "近期动态",
            "activity_subtitle": "老师反馈、课堂讨论与最新提醒。",
            "priority_unread_title": "消息中心有未读消息",
            "priority_unread_description": "可能包含批改反馈、课堂通知或同伴私信。",
            "priority_empty_title": "眼下没有急迫待办",
            "priority_empty_description": "节奏很稳，可以顺手复盘错题或预习下一节材料。",
            "action_priority_label": "查看优先事项",
            "action_priority_description": "直接处理当前最临近的任务。",
            "action_message_label": "消息中心",
            "action_message_description": "查看批改反馈、课堂通知与私信。",
            "action_security_label": "修改密码",
            "action_security_description": "定期更新密码，保护账号安全。",
            "empty_title": "暂时还没有已加入的课堂",
            "empty_description": "任课老师开课后，课堂入口会自动出现在这里。",
            "empty_action_label": "去消息中心",
            "empty_step_profile_title": "核对个人资料",
            "empty_step_profile_description": "确认姓名、班级和联系方式准确，方便匹配课程名单。",
            "empty_step_profile_label": "去个人中心",
            "empty_step_classroom_title": "等待老师开课",
            "empty_step_classroom_description": "任课老师开设课堂后，这里会自动显示课程入口。",
            "empty_step_message_title": "留意消息中心",
            "empty_step_message_description": "开课通知、作业提醒与批改结果都会同步到这里。",
            "empty_step_message_label": "去消息中心",
        },
    },
    "classroom": {
        "teacher": {
            "hero_eyebrow": "课程课堂",
            "hero_lead": "{{name}}，这门课的任务、材料、资源和研讨都在这里。",
            "assignment_title": "作业与考试",
            "assignment_subtitle": "布置任务、查看提交进度并及时批改。",
            "assignment_empty_title": "这门课还没有发布任务",
            "assignment_empty_description": "可以新建一份作业，或从试卷库直接安排考试。",
            "materials_title": "课程材料",
            "materials_subtitle": "学生阅读和复习用的学习文档都在这里。",
            "resources_title": "课堂资源与工具",
            "resources_subtitle": "课件、示例文件与常用工具下载。",
            "discussion_title": "课堂研讨",
            "discussion_subtitle": "发布提醒、回应提问、和全班实时交流。",
            "discussion_detail_template": "一句引导或一个真实案例，往往就能带起整堂课的讨论。",
            "spotlight_draft_label": "待发布草稿",
            "spotlight_draft_note": "有任务还停留在草稿状态，补充完整即可向学生发布。",
            "spotlight_active_label": "进行中的任务",
            "spotlight_active_note": "任务已经发出，可以随时查看提交并补充课程材料。",
        },
        "student": {
            "hero_eyebrow": "课程课堂",
            "hero_lead": "{{name}}，这门课的任务、材料、资源和研讨都在这里。",
            "assignment_title": "我的作业与考试",
            "assignment_subtitle": "看清要求与截止时间，按节奏完成并提交。",
            "assignment_empty_title": "这门课暂时还没有新任务",
            "assignment_empty_description": "老师发布作业或考试后，入口会第一时间出现在这里。",
            "materials_title": "课程材料",
            "materials_subtitle": "老师分享的讲义与学习文档都在这里。",
            "resources_title": "课堂资源与工具",
            "resources_subtitle": "课件、工具与实验参考资料。",
            "discussion_title": "课堂研讨",
            "discussion_subtitle": "有疑问随时发问，有想法也欢迎分享。",
            "discussion_detail_template": "哪怕只是半截想法，说出来也常常能帮彼此理清思路。",
            "spotlight_pending_label": "待提交任务",
            "spotlight_pending_note": "优先完成尚未提交的作业或考试。",
            "spotlight_submitted_label": "已提交",
            "spotlight_submitted_note": "作业已提交，记得留意后续的批改与评语。",
            "spotlight_empty_label": "暂无待交任务",
            "spotlight_empty_note": "老师发布新任务时，这里会第一时间提醒你。",
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
