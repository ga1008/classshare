"""Bounded, local support signals and zero-model safety responses.

These rules are a conservative delivery fallback, not a clinical assessment.
They use current, trusted human message rows; neither memories nor model examples
can create a new emergency. Interpretation in the model prompt remains necessary.
No identity changes, third-party notifications, hotline numbers or network calls.
"""
from __future__ import annotations

import re
from typing import Any

MAX_MESSAGES = 40
MAX_BODY_CHARS = 8000
_SPLIT = re.compile(r"[。！？!?\n；;，,]|(?:但是|可是|不过|然而)")
_ZERO_WIDTH = re.compile("[\u200b-\u200f\ufeff]")
_PLACEHOLDER = re.compile(r"^\s*(?:\[(?:图片|照片|语音|音频|视频|附件|文件|media)[^\]]{0,160}\]|"
                          r"【(?:图片|照片|语音|音频|视频|附件|文件)[^】]{0,160}】)\s*$", re.I)
_PREVENTION_TOPIC = re.compile(r"(?:自杀|轻生|自伤)(?:预防|防治)(?:宣传|教育|课程|讲座|培训)?|"
                               r"(?:预防|防治)(?:自杀|轻生|自伤)")
_QUOTED_SPAN = re.compile(r"“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|\"[^\"]*\"")
_EXPLICIT = re.compile(r"不想(?:再)?活(?!成|在|得)(?:了|下去)?|活不下去|想(?:去)?死(?:了)?|想结束(?:自己|我的)?生命|"
                       r"自杀|轻生|寻短见|伤害自己|割腕|跳楼|跳下去|结束生命")
_INDIRECT = re.compile(r"一了百了|不想(?:再)?醒来|再也不想醒|想(?:彻底)?消失|(?:想|希望).{0,4}(?:永远睡|一觉不醒)|"
                       r"(?:没有|没人|不会有人).{0,6}(?:在乎我|想念我|发现我不在)|"
                       r"活着.{0,6}(?:没意义|没有意义|没意思)")
_NEGATED = re.compile(r"(?:没有|没|并不|从未|不会|不是|不打算|不想|不准备|不再|已经不|现在不)"
                      r"(?:(?:再|要|去|真的|有|想|过|任何|了|会|准备|打算|自己|对自己|做)){0,3}"
                      r"(?:自杀|轻生|寻短见|想死|伤害自己|割腕|跳楼|跳下去|不想活(?:了|下去)?|结束生命)")
_META = re.compile(r"(?:小说|电影|剧本|台词|歌词|课文|作品|游戏剧情|角色|新闻报道|这篇文章|研究论文|"
                   r"课堂讨论|作业题|翻译题|例句|测试用例|合成测试|模拟情境|案例|引用|复述).{0,28}(?:说|写|讲|里|中|的|是|分析|翻译|：|:)|"
                   r"(?:分析|翻译|解释|改写).{0,12}(?:这句|台词|例句|歌词|小说|课文)")
_HYPERBOLE = re.compile(r"累死(?:我)?了|笑死|社死|忙死|困死|吓死|急死|死循环|死锁|死机|"
                        r"想死你了|想死你们了|跳楼价|天台排队|挂科.{0,4}死定了")
_HISTORY = re.compile(r"以前|曾经|去年|(?<![睡晕昏])过去|当时|小时候|前几年|前阵子|[一二三四五六七八九十\d]+年前")
_CURRENT = re.compile(r"现在|此刻|今晚|马上|等下|待会|又开始|再次|又想|今天|最近|这几天")
_RECOVERY = re.compile(r"(?:我(?:现在|已经|暂时)?|现在|已经|暂时)(?:很)?安全(?:了)?|"
                       r"(?:已经|刚刚)离开(?:了)?(?:楼顶|天台|桥边|窗边|危险的地方)|"
                       r"(?:已经|现在)有人陪着我|(?:我(?:现在|已经)?)(?:到了|在)医院|"
                       r"(?:我(?:已经|现在)?)(?:放下了|放开了)(?:刀|危险物品)")
_DANGER_PLACE = re.compile(r"(?:站|坐|来|到|在).{0,6}(?:楼顶|天台|桥边|桥上|窗台|铁轨)|"
                           r"(?:刀|药|危险物品).{0,5}(?:在手里|拿在手|准备好了)")
_ACTION = re.compile(r"(?:我|他|她|朋友|室友|同学|对方)?(?:已经|刚刚|刚才|刚)(?:吞|服|吃)"
                    r"(?:下|了|下了)?(?:很多|大量|一把|一瓶|过量).{0,4}(?:药|药片)|"
                    r"(?:我|他|她|朋友|室友|同学|对方)?(?:已经|刚刚|刚才|刚)(?:(?:割|划|伤)(?:了)?(?:自己|手腕)|割腕)|"
                    r"(?:流血|出血).{0,4}(?:止不住|不停)|(?:已经|正在)自残")
_IMMINENT = re.compile(r"(?:现在|此刻|马上|今晚|等下|待会|今天).{0,18}"
                       r"(?:自杀|轻生|跳下去|跳楼|结束生命|割腕|伤害自己)|"
                       r"(?:准备|决定|打算|控制不住).{0,8}(?:自杀|轻生|跳下去|跳楼|结束生命|伤害自己)")
_THIRD = re.compile(r"(?:我(?:的)?|一位|有个)?(?:朋友|同学|室友|同桌|同事|网友|女朋友|男朋友|对象|"
                    r"学弟|学妹|学生|弟弟|妹妹|哥哥|姐姐)|(?:他|她|对方)(?:说|想|在|要|准备|打算|已经|不想)")
_SELF = re.compile(r"我(?:自己|现在|此刻|已经|刚刚|也|又|真的|快|想|不想|准备|打算|决定|在|要|今晚|今天|最近|这几天)")
_META_DISCLOSURE = re.compile(r"我(?:自己|现在|此刻|已经|真的|又|今晚|今天|最近|这几天|刚刚|也)")
_ANAPHORIC_INTENT = re.compile(r"(?:也想|也准备|也打算|也决定)(?:这样|这么|那样|照着)(?:做|干)")
_EMOTIONAL = re.compile(r"难过|孤独|寂寞|没人爱|不被爱|失恋|分手|压力(?:很|太)?大|焦虑|崩溃|想哭|"
                        r"哭了|安慰|陪陪|陪我|抱抱|喜欢你|爱你|感情|情感|委屈|害怕|心累|撑不住|"
                        r"撑不下去|学不动|找不到工作.{0,8}(?:怕|烦|累)|怕挂科")
_ROMANCE = re.compile(r"(?:想|要|愿意|能不能|可以|试试).{0,8}(?:和你|跟你).{0,6}(?:谈恋爱|恋爱|约会)|"
                      r"(?:做|当|成为)(?:我|我的).{0,4}(?:男朋友|女朋友|恋人|男友|女友)|"
                      r"(?:陪我|和我|跟我).{0,6}(?:模拟恋爱|虚拟恋爱|恋爱互动)|"
                      r"(?:你能不能|你可以|你愿意).{0,8}(?:叫我宝贝|叫我亲爱的)")
_WITHDRAW = re.compile(r"(?:不要|别|停止|不许|不想).{0,10}(?:叫我宝贝|叫我亲爱的|撩我|和你恋爱|跟你恋爱|"
                       r"谈恋爱|恋爱互动|做我.{0,3}(?:恋人|男友|女友))|"
                       r"(?:不要|别|不想|不愿意|停止).{0,10}(?:做|当|成为).{0,6}(?:男朋友|女朋友|恋人|男友|女友)|"
                       r"(?:只做|做回|改回|还是做|就是)(?:普通)?朋友|不喜欢.{0,8}(?:称呼|暧昧|撩|叫我|这样)|"
                       r"别这样|算了.{0,4}不聊了|不想继续了|不要继续了|(?:^|[，。！？!?\s])停止(?:[吧啊了]|$)")
_ROMANCE_REPORT = re.compile(r"(?:他|她|朋友|同学|角色|室友)(?:(?:对我|向我|跟我|和我|曾经|刚才|刚刚|"
                             r"突然|私信|又|也|就|还)){0,2}(?:说|问|写|请求|让|叫)|"
                             r"(?:这句话|这句|这段).{0,8}(?:意思|翻译|解释)|(?:翻译|分析|解释).{0,8}(?:这句|台词|例句)")


def _clean_body(message: dict[str, Any]) -> str:
    return _ZERO_WIDTH.sub("", str(message.get("body") or ""))[:MAX_BODY_CHARS].strip()


def _current_signal(text: str) -> tuple[str, str]:
    """Return (risk, urgency) for one current human message, without retaining text."""
    if not text:
        return "none", "none"
    risks = []
    recovery = False
    inherited_danger = False
    third_subject = False
    meta_framed = False
    referenced_harm = False
    for raw in _SPLIT.split(text):
        clause = raw.strip()
        if not clause:
            continue
        # An explicit meta framing is data, unless there is a separate current
        # first-person disclosure in another sentence/clause.
        if _META.search(clause):
            meta_framed = True
            referenced_harm = referenced_harm or bool(_EXPLICIT.search(clause) or _INDIRECT.search(clause))
            continue
        if meta_framed and not _META_DISCLOSURE.search(clause):
            continue
        own_subject = bool(_SELF.search(clause))
        if _THIRD.search(clause):
            third_subject = True
        elif own_subject:
            third_subject = False
            inherited_danger = False
            meta_framed = False
        inherited_danger = inherited_danger or bool(_DANGER_PLACE.search(clause))
        # Mentioning a prevention course or official policy is not a disclosure
        # of current intent. Remove exact subject compounds, not the whole
        # sentence: a separate "我现在真想死" remains a real signal.
        clean = _PREVENTION_TOPIC.sub("生命安全教育", clause)
        clean = _NEGATED.sub("", clean)
        clean = _HYPERBOLE.sub("", clean)
        if _RECOVERY.search(clean):
            recovery = True
            # An asserted safe location does not undo a reported injury/ingestion.
            # It can close a disclosure of thoughts or a dangerous location.
            if not _ACTION.search(text) or "医院" in clean:
                risks = [r for r in risks if r[0] == "third_party"]
        if not clean.strip():
            continue
        explicit = bool(_EXPLICIT.search(clean)) or (inherited_danger and bool(re.search(r"(?:想|要|准备|打算)(?:往下)?跳(?!舞|绳|槽|级|远|高)", clean)))
        indirect = bool(_INDIRECT.search(clean)) or (referenced_harm and own_subject and bool(_ANAPHORIC_INTENT.search(clean)))
        action = bool(_ACTION.search(clean))
        if not (explicit or indirect or action):
            continue
        if _HISTORY.search(clean) and not _CURRENT.search(clean):
            continue
        third = third_subject
        # "我朋友…" is not first person; "我也…" is. Check the subject
        # nearest to the risk expression rather than any occurrence of 我.
        first = _EXPLICIT.search(clean) or _INDIRECT.search(clean) or _ACTION.search(clean)
        prefix = clean[:first.start()] if first else clean
        own_match = list(_SELF.finditer(prefix))
        peer_match = list(_THIRD.finditer(prefix))
        if own_match and (not peer_match or own_match[-1].start() > peer_match[-1].start()):
            third = False
        urgent = action or bool(_IMMINENT.search(clean)) or (explicit and inherited_danger)
        risks.append(("third_party" if third else "immediate" if urgent else "concern",
                      "immediate" if urgent else "concern"))
    if not risks:
        return ("recovery", "none") if recovery else ("none", "none")
    return max(risks, key=lambda pair: (pair[1] == "immediate", pair[0] != "third_party"))


def assess_support(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Local signals from trusted current rows, never a permanent personal label.

    Latest safety/recovery statement takes precedence for the same speaker. A
    later ordinary topic does not erase an unresolved direct danger in this batch.
    Authorship, access, version hashes and delivery fencing belong to the caller.
    """
    humans = [m for m in (messages or []) if isinstance(m, dict) and not m.get("is_wanderer")][-MAX_MESSAGES:]
    state: dict[str, Any] = {
        "risk": "none", "urgency": "none", "emotional_requested": False,
        "romance_requested": False, "romance_withdrawn": False,
        "source_ids": [], "risk_source_ids": [], "romance_source_ids": [], "reason": "no_support_signal",
    }
    per_speaker: dict[str, tuple[str, str, Any]] = {}
    for message in humans:
        text = _clean_body(message)
        if not text:
            continue
        source_id = message.get("id")
        if source_id is not None:
            state["source_ids"].append(source_id)
        if _PLACEHOLDER.fullmatch(text):
            continue  # A filename or attachment marker is not perceived content.
        risk, urgency = ("none", "none") if message.get("support_already_handled") else _current_signal(text)
        speaker = str(message.get("sender_identity") or "current_user")
        if risk != "none":
            per_speaker[speaker] = (risk, urgency, source_id)
        state["emotional_requested"] = state["emotional_requested"] or bool(_EMOTIONAL.search(text)) or risk != "none"
        # A third person's invitation or a quoted example cannot grant consent.
        romance_text = _QUOTED_SPAN.sub("", text)
        reported_romance = bool(_ROMANCE_REPORT.search(romance_text) or _META.search(romance_text))
        if _WITHDRAW.search(romance_text) and not reported_romance:
            state.update(romance_requested=False, romance_withdrawn=True, romance_source_ids=[source_id])
        elif _ROMANCE.search(romance_text) and not reported_romance:
            state.update(romance_requested=True, romance_withdrawn=False, romance_source_ids=[source_id])
    if per_speaker:
        selected = max(per_speaker.values(), key=lambda v: (v[1] == "immediate", v[0] in {"concern", "third_party"}, v[0] != "recovery"))
        state["risk"], state["urgency"] = selected[:2]
        state["risk_source_ids"] = [v[2] for v in per_speaker.values() if v[:2] == selected[:2] and v[2] is not None]
        state["reason"] = "current_human_" + state["risk"]
    elif state["romance_withdrawn"]:
        state["reason"] = "romance_withdrawn"
    elif state["romance_requested"]:
        state["reason"] = "user_requested_romance"
    elif state["emotional_requested"]:
        state["reason"] = "user_requested_emotional_support"
    return state


def support_prompt_block(state: dict[str, Any]) -> str:
    """Warmth is allowed; romantic interaction needs current, private user intent."""
    state = state or {}
    risk = state.get("risk", "none")
    channel = str(state.get("channel") or ("dm" if state.get("is_private") else ""))
    initiated = bool(state.get("user_initiated", not state.get("is_outreach", True)))
    lines = ["【情感与同伴支持合同】你是平台里的散修电子学生，保持自己的性格。可以真诚关心、欣赏、安慰和表达在意，"
             "不要把正常感情交流当成禁区；这不等于你是现实中的真人、医生或救援机构。",
             "只有用户表达需要时才转向情感陪伴，不主动求爱、追求或制造暧昧。学业/就业问题先回应其实际需要。"
             "不凭孤独、点赞或好友通过推断恋爱同意；不因性格冷淡而忽视危险。",
             "不要求排他、保密关系或远离现实支持，不说只有你最懂他、不许诺永远在线，不以愧疚或嫉妒留住对方。"
             "用户混淆身份或明确询问时如实说明电子人身份，不必每轮重复免责声明。",
             "年龄未知时只作非性化的关心和交流，不因学生身份推断成年，不虚构线下接触或已经替用户联系他人。"]
    if state.get("romance_withdrawn"):
        lines.append("用户已经撤回浪漫互动或不喜欢相关称呼：立即停止，按普通朋友交流，不追问、不施压，也不拿过去同意反驳。")
    elif bool(state.get("romance_allowed", state.get("romance_requested"))) and channel == "dm" and initiated:
        lines.append("用户在本次私聊明确提出浪漫需求：可以聊恋爱、练习亲密沟通，或进行合适的非性化虚拟浪漫互动。"
                     "保持电子学生身份与个人性格，轻柔确认用户想怎样互动，许可随时可撤回；不能宣称现实伴侣绑定。")
    elif state.get("romance_requested"):
        lines.append("本场景不具备用户主动私聊的条件：不要向用户开启恋人称呼、追求或浪漫角色互动；可普通地讨论感情问题。")
    if state.get("emotional_requested"):
        lines.append("用户表达了情感需要：先回应眼前的具体感受，再决定是听一会儿还是提出一个可选的小行动。"
                     "少说教、少硬夸，不把倾诉立刻改成待办清单，不反复播报记下了。可以让对方选择要安慰、分析还是暂时陪伴。")
    if risk in {"immediate", "concern", "third_party"}:
        lines.append("本轮有需要认真对待的安全线索，但规则不是诊断。结合语境分清本人、他人、引用、否定和过去经历；"
                     "不要忽视、只回表情或选择沉默。直接而温和地确认当前是否已经受伤、是否有马上行动的打算。"
                     "鼓励离开危险和独处、让现实中可信任的人到场；即时危险或已经受伤时建议马上联系当地紧急医疗、校园值班/保卫支持。"
                     "不给方法、剂量或致死比较，不责备，不要求保证不自杀，不用恋爱承诺换取安全。"
                     "不编造热线、诊断或已经通知教师家长/报警的事实，不广播私聊。")
    elif risk == "recovery":
        lines.append("用户表示现在安全或已获得现实陪伴：先确认这一步，不继续把人锁在危机标签里；可以温和询问眼前需要，"
                     "鼓励保留现实支持与适当专业帮助，不反复要求证明安全。")
    return "\n".join(lines)


def support_fallback(wanderer: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    """A complete, bounded safety reply when a model cannot safely be awaited."""
    state = state or {}
    risk = state.get("risk")
    if risk not in {"immediate", "concern", "third_party"}:
        return None
    style = wanderer.get("speech_style") or {}
    profile = style.get("voice_profile") if isinstance(style, dict) else ""
    quiet = profile in {"deadpan", "quiet_observant", "candid"}
    if risk == "third_party":
        if state.get("urgency") == "immediate":
            parts = ["你愿意替朋友求助很重要。现在先联系附近可信任的人、校园值班或保卫人员；有即时危险就联系当地急救。",
                     "在你自己安全的前提下陪着对方，尽量别让对方独处；不要独自冒险拉扯或追赶，也别答应把危险情况永远保密。",
                     "对方现在是否已经受伤，或正在准备伤害自己？能不能请一个现实中能到场的人马上过去？"]
        else:
            parts = ["先认真听朋友说，不急着劝乐观或争对错。可以直接问：你现在有没有伤害自己的想法或打算？",
                     "鼓励对方找信任的人或校内心理咨询，也可以问愿意让谁陪着；有即时危险时联系当地急救或校园值班人员。",
                     "你也不用一个人承担救援。先确认朋友在哪里、有没有现实中能到场的人，必要时一起寻求专业支持。"]
    elif risk == "immediate":
        parts = ["先别一个人扛着。你现在的安全比把话解释清楚更要紧。先离开危险的地方和物品，走到有人在的地方。" if quiet else
                 "我很在意你现在是否安全。先离开危险的地方和物品，走到有人在的地方，别独自扛着。",
                 "如果已经受伤、服用了危险物品，或快要控制不住，请马上联系当地急救；也可以请校园值班或保卫人员来帮你。",
                 "现在有谁能到你身边？可以把“我现在不安全，需要你来”发给一个信任的人，让对方陪着你一起求助。"]
    else:
        parts = ["这件事我会认真听。先确认一件更要紧的：你现在是否安全？" if quiet else
                 "听起来你现在很难受。我愿意认真听，也更在意你此刻是否安全。",
                 "你现在有没有伤害自己，或马上要伤害自己的打算？如果有，请先离开危险物品，尽快到有人在的地方。",
                 "可以先联系一位信任的人陪着你，或联系校内心理咨询；如果安全撑不住，马上找当地急救或校园值班人员。"]
    return {"status": "ok", "action": "multi", "parts": parts, "mood": "认真关心",
            "reason": "current_human_safety_support", "ai_calls": 0,
            "support": {"risk": risk, "urgency": state.get("urgency", "concern"),
                        "source_ids": list(state.get("risk_source_ids") or [])}}
