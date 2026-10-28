"""Student-led academic, career and emotional support, shared by reply scenes.

Only server-supplied current messages select a lane. History, retrieved documents
and inferred private profiles cannot grant relationship consent or trigger a crisis.
"""
from __future__ import annotations

from typing import Any

ACADEMIC_TERMS = ("学分", "选课", "课程", "考试", "挂科", "补考", "重修", "学校规定", "校规", "学籍", "毕业要求",
                  "请假", "奖学金", "助学", "公文", "通知", "作业", "论文", "学习", "复习", "保研", "考研")
CAREER_TERMS = ("就业", "求职", "招聘", "岗位", "简历", "面试", "实习", "薪资", "薪水", "工资", "福利", "offer",
                "加班", "同事", "领导", "职场", "公司", "转正", "试用期", "毕业后", "职业", "劳动合同", "社保")
EMOTIONAL_TERMS = ("压力", "难过", "焦虑", "孤独", "孤单", "烦", "失恋", "分手", "喜欢的人", "恋爱", "在意我",
                   "关心我", "抱抱", "陪陪", "陪我", "心累", "委屈", "害怕", "不开心", "没人在乎", "被爱")


def _channel(scene: dict, key: str) -> str:
    kind = str(scene.get("kind") or "")
    if key == "blog_draft" or "写博客" in kind:
        return "post"
    if "想起一位好友" in kind or "主动" in kind or key.startswith("social:friend:"):
        return "outreach"
    if "课堂" in kind or key.startswith("classroom:"):
        return "classroom"
    if scene.get("scene_type") == "group_chat" or "群聊" in kind:
        return "group"
    if scene.get("scene_type") == "social_chat" or "私聊" in kind or "私信" in kind or key.startswith("dm:"):
        return "dm"
    return "comment"


def prepare_support_scene(wanderer: dict, scene: dict, thread_key: str) -> dict:
    from .wanderer_support_service import assess_support, support_prompt_block
    from .wanderer_student_context import needs_academic_lookup, needs_career_lookup
    if "_student_support_state" in scene:
        return scene["_student_support_state"]
    messages = [m for m in scene.get("_support_messages") or [] if isinstance(m, dict)
                and not m.get("is_wanderer") and not m.get("recalled_at")
                and m.get("sender_identity") != f"student:{wanderer.get('student_id')}"
                and str(m.get("body") or "").strip()]
    state = dict(assess_support(messages))
    channel = _channel(scene, thread_key)
    identity = str(scene.get("_support_user_identity") or "")
    # A mixed public conversation must not combine different people's preferences.
    current = [m for m in messages if not identity or m.get("sender_identity") == identity]
    text = "\n".join(str(m.get("body") or "") for m in current)[-4000:]
    low = text.lower()
    topics = []
    if needs_academic_lookup(text) or any(word.lower() in low for word in ACADEMIC_TERMS):
        topics.append("academic")
    if needs_career_lookup(text) or any(word.lower() in low for word in CAREER_TERMS):
        topics.append("career")
    if (any(word in text for word in EMOTIONAL_TERMS) or state.get("emotional_requested")
            or state.get("romance_requested") or state.get("risk") not in (None, "none")):
        topics.append("emotional")
    initiated = bool(messages) and channel not in {"outreach", "post"}
    romance_allowed = (channel == "dm" and initiated and bool(state.get("romance_requested"))
                       and not state.get("romance_withdrawn") and state.get("risk", "none") == "none")
    state.update(channel=channel, user_initiated=initiated, topics=topics, current_text=text,
                 user_identity=identity, romance_allowed=romance_allowed, age_status="unknown")
    blocks = [
        "【学生支持方式】学业、就业、情感是可用的帮助方向，不是每句闲聊都要安排成咨询。"
        "沿用自己的性格和电子学生身份，可以有温度、有不同意见和真实的角色表达；不自称学校审批人、招聘方或心理治疗师。"
        "先看对方此刻想解决问题，还是希望有人听一会儿；混合压力先接住感受，再问是否需要一起想一个小步骤。"
        "别把支持变成督促打卡、套话鼓励或要求交出隐私。对方说不想聊就停，不拿历史记忆替代本轮意愿。",
        support_prompt_block(state),
    ]
    if any(m.get("support_already_handled") for m in messages):
        blocks.append("【已回应的安全信息】部分较早消息已发送过必要安全支持，原文仅作理解背景。"
                      "不要机械复读整套安全话术；关注之后的新问题、澄清与真实安全进展。"
                      "如果本轮有新的危险表达，仍认真回应并帮助连接现实支持。")
    if "academic" in topics:
        blocks.append("【学业】把知识讲到对方当前能用的程度，区分理解方法、学分规则和正式办事要求。"
                      "具体校规必须以当前检索到的适用学校正式来源为依据，写清文名、日期、适用范围和出处。"
                      "只见标题或未解析正文时不能推断条款；来源过旧、冲突、学校不明或没有命中就坦白，"
                      "可以帮整理向教务/辅导员确认的问题，不替学校保证学分、审批或毕业资格。")
    if "career" in topics:
        blocks.append("【就业】结合对方向你可见的学院、专业、课程、兴趣和主动提供的目标，信息缺失时只问最关键的一项。"
                      "先确认预期毕业时间、地点或方向；不能根据班级编号猜四年学制或毕业年。"
                      "把毕业时前景写成有条件的情景推演：当前证据、可能变化、可迁移能力、接下来可验证的小行动，"
                      "不保证岗位、工资或行业未来，不把少量岗位当统计趋势，未实时查网不能声称查到了最新行情。"
                      "可按需要讨论简历成果与证据、岗位筛选、面试、核验公司、福利与工作边界、同事领导沟通；"
                      "建议具体但不编造经历，不虚构投递、offer或公司评价。劳动合同/福利争议是高风险问题，"
                      "应查看当前适用官方规则并建议向学校就业服务或合格专业人士确认，不凭记忆下法律结论。"
                      "简历和职业模块只提供用户本人操作入口，不擅自读取私有简历、写入或投递。")
    scene["_student_support_state"] = state
    scene["_student_support_block"] = "\n\n".join(b for b in blocks if b)
    return state


def fallback_for_scene(wanderer: dict, scene: dict, thread_key: str, *, on_failure: bool = False) -> dict | None:
    from .wanderer_support_service import support_fallback
    state = prepare_support_scene(wanderer, scene, thread_key)
    if not state["user_initiated"]:
        return None
    immediate = state.get("urgency") == "immediate" or state.get("risk") == "immediate"
    if not immediate and not on_failure:
        return None
    response = support_fallback(wanderer, state)
    if not response:
        return None
    parts = list(response.get("parts") or [])
    return {"status": "ok", "turn": {"action": response.get("action") or ("multi" if len(parts) > 1 else "reply"),
            "parts": parts, "mood": str(response.get("mood") or "关切"), "like": False,
            "reason": str(response.get("reason") or "优先回应当前安全需求")},
            "raw_text": "", "ai_calls": 0, "support_fallback": True,
            "support_evidence_ids": state.get("risk_source_ids") or state.get("evidence_ids") or []}


def prepare_student_support(conn: Any, wanderer: dict, thread_key: str, scene: dict) -> str:
    state = prepare_support_scene(wanderer, scene, thread_key)
    if scene.get("_student_support_sources_prepared"):
        return scene["_student_support_block"]
    scene["_student_support_sources_prepared"] = True
    if (state["user_initiated"] and state.get("risk") not in {"immediate", "concern", "third_party"}
            and any(t in state["topics"] for t in ("academic", "career"))):
        from .wanderer_student_context import build_student_support_context
        context = build_student_support_context(conn, wanderer, scene, message=state["current_text"],
                                                user_identity=state["user_identity"])
        if context:
            scene["_student_support_block"] += "\n\n" + context
    return scene["_student_support_block"]
