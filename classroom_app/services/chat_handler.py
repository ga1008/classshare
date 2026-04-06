import json
import sys
import random
import asyncio  # 导入 asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import deque
from fastapi import WebSocket

from ..core import chat_histories, chat_log_lock
from ..config import CHAT_LOG_DIR, STUDENT_HISTORY_COUNT, TEACHER_HISTORY_COUNT, MAX_HISTORY_IN_MEMORY
from ..database import get_db_connection

# 刷新去抖的延迟时间 (秒)
REFRESH_DEBOUNCE_SECONDS = 5

# 临时名字库 (100个名字，涵盖武侠小说、世界名人、古代人物、三体等)
TEMPORARY_NAMES = [
    # 武侠小说人物
    "令狐冲", "杨过", "小龙女", "张无忌", "赵敏", "黄蓉", "郭靖", "周芷若", "乔峰", "段誉",
    "虚竹", "王语嫣", "东方不败", "任我行", "岳不群", "风清扬", "萧峰", "慕容复", "胡斐", "程灵素",
    # 世界知名人物
    "达芬奇", "爱因斯坦", "牛顿", "特斯拉", "图灵", "居里夫人", "霍金", "伽利略", "莎士比亚", "贝多芬",
    "莫扎特", "梵高", "毕加索", "亚里士多德", "柏拉图", "苏格拉底", "拿破仑", "凯撒", "哥伦布", "南丁格尔",
    # 古代人名
    "诸葛亮", "曹操", "刘备", "孙权", "关羽", "张飞", "赵云", "周瑜", "司马懿", "吕布",
    "貂蝉", "王昭君", "西施", "杨玉环", "李白", "杜甫", "白居易", "苏轼", "王安石", "李清照",
    # 三体人物
    "叶文洁", "罗辑", "章北海", "云天明", "程心", "史强", "汪淼", "丁仪", "申玉菲", "魏成",
    # 科幻与文学
    "哈利波特", "赫敏", "邓布利多", "甘道夫", "弗罗多", "阿拉贡", "孙悟空", "唐僧", "猪八戒", "沙僧",
    # 现代人物
    "马斯克", "乔布斯", "扎克伯格", "马云", "马化腾", "李彦宏", "雷军", "任正非", "董明珠", "王健林",
    # 神话与传说
    "宙斯", "雅典娜", "阿波罗", "奥丁", "索尔", "洛基", "女娲", "伏羲", "神农", "黄帝",
    # 动物与自然
    "闪电豹", "啸天狼", "追风马", "踏雪狐", "凌霄鹰", "深海鲸", "丛林虎", "沙漠狐", "草原狮", "雪山熊",
    # 颜色与元素
    "青莲剑", "紫电侠", "赤炎刀", "碧水镜", "黄金甲", "白银枪", "黑曜石", "翡翠心", "琥珀眼", "珊瑚枝"
]


class MultiRoomConnectionManager:
    """
    管理多个独立的聊天室 (每个 class_offering_id 对应一个房间)
    """

    def __init__(self):
        # 结构: {class_offering_id: {client_id: WebSocket}}
        self.rooms: Dict[int, Dict[str, WebSocket]] = {}
        # 结构: {client_id: user_info}
        self.user_info: Dict[str, dict] = {}
        # 结构: {client_id: room_id}
        self.client_to_room: Dict[str, int] = {}
        # 新增: 跟踪延迟的“离开”任务，用于去抖
        # 结构: {client_id: asyncio.Task}
        self.disconnect_tasks: Dict[str, asyncio.Task] = {}
        # 临时名字分配跟踪
        # 结构: {room_id: set(已分配名字)}
        self.room_assigned_names: Dict[int, set] = {}
        # 名字池副本，便于随机选择
        self.name_pool = TEMPORARY_NAMES.copy()
        # 用户临时名字映射: (room_id, client_id) -> display_name
        self.user_assigned_names: Dict[Tuple[int, str], str] = {}

    def get_room_id(self, websocket: WebSocket) -> int:
        """从 websocket scope 中安全获取 room_id"""
        return int(websocket.path_params.get("class_offering_id", 0))

    def assign_temporary_name(self, room_id: int, user: dict, client_id: str) -> str:
        """为学生分配临时名字，教师保留真实姓名。相同用户在同一房间中保持相同临时名字。"""
        # 教师始终使用真实姓名
        if user.get('role') == 'teacher':
            display_name = user.get('name', '教师')
            self.user_assigned_names[(room_id, client_id)] = display_name
            return display_name

        # 检查是否已有分配
        key = (room_id, client_id)
        if key in self.user_assigned_names:
            return self.user_assigned_names[key]

        # 确保房间的名字分配集合存在
        if room_id not in self.room_assigned_names:
            self.room_assigned_names[room_id] = set()

        assigned_names = self.room_assigned_names[room_id]
        available_names = [name for name in self.name_pool if name not in assigned_names]

        # 如果没有可用的名字，则从已分配中随机选择一个（虽然这种情况很少见）
        if not available_names:
            available_names = list(self.name_pool)

        # 随机选择一个名字
        chosen_name = random.choice(available_names)
        assigned_names.add(chosen_name)
        self.user_assigned_names[key] = chosen_name

        return chosen_name

    async def connect(self, websocket: WebSocket, user: dict):
        await websocket.accept()
        client_id = user['id']
        room_id = self.get_room_id(websocket)

        if room_id == 0:
            await websocket.close(reason="Invalid room ID")
            return

        # 检查这是否是一次快速重连 (刷新)
        is_reconnect = False
        if client_id in self.disconnect_tasks:
            # 是刷新！获取延迟的”离开”任务
            task = self.disconnect_tasks.pop(client_id)
            # 取消这个任务，这样 “XXX 离开了” 的消息就不会被发送
            task.cancel()
            is_reconnect = True
            print(f"[CHAT] 阻止了 {user['name']} (ID: {client_id}) 的刷新刷屏。")

        # 分配临时名字（学生使用临时名字，教师保留真实姓名）
        original_name = user.get('name', '用户')
        display_name = self.assign_temporary_name(room_id, user, client_id)

        # 创建用户副本，包含显示名字和原始名字
        user_copy = user.copy()
        user_copy['display_name'] = display_name
        user_copy['original_name'] = original_name

        if room_id not in self.rooms:
            self.rooms[room_id] = {}
            load_chat_history_for_room(room_id)  # 首次有人进入时，加载历史

        self.rooms[room_id][client_id] = websocket
        self.user_info[client_id] = user_copy
        self.client_to_room[client_id] = room_id

        # 发送历史记录
        history_to_send = self.get_history_for_user(room_id, user_copy)
        if history_to_send:
            await websocket.send_text(json.dumps({"type": "history", "data": history_to_send}))

        # 发送当前用户的显示名字，供前端识别自己的消息
        await websocket.send_text(json.dumps({"type": "user_display_name", "display_name": display_name}))

        # 刷新用户列表是必须的，也是安全的 (不会刷屏)
        await self.broadcast_user_list(room_id)

        # 只有在不是刷新的情况下，才广播”加入了课堂”
        if not is_reconnect:
            # 系统消息中显示显示名字
            await self.broadcast(room_id, json.dumps({"type": "system", "message": f"{display_name} 加入了课堂。"}))

    def get_history_for_user(self, room_id: int, user: dict) -> List[dict]:
        history_count = TEACHER_HISTORY_COUNT if user['role'] == 'teacher' else STUDENT_HISTORY_COUNT
        room_history = chat_histories.get(room_id, deque())
        return list(room_history)[-history_count:]

    async def disconnect(self, websocket: WebSocket, client_id: str):
        room_id = self.client_to_room.get(client_id, 0)
        if room_id == 0: return

        if client_id in self.rooms[room_id]:
            del self.rooms[room_id][client_id]

        user_info = self.user_info.pop(client_id, {})
        user_name = user_info.get('display_name', user_info.get('name', '一位用户'))
        del self.client_to_room[client_id]

        # 立即更新用户列表
        await self.broadcast_user_list(room_id)

        # *** 不立即广播离开消息 ***
        # 而是创建一个延迟任务
        task = asyncio.create_task(
            self.delayed_leave_broadcast(room_id, client_id, user_name)
        )
        self.disconnect_tasks[client_id] = task

    async def delayed_leave_broadcast(self, room_id: int, client_id: str, user_name: str):
        """
        延迟广播用户离开的消息。
        如果用户在这期间重连，此任务将被 connect() 方法取消。
        """
        try:
            # 等待 REFRESH_DEBOUNCE_SECONDS 秒
            await asyncio.sleep(REFRESH_DEBOUNCE_SECONDS)

            # 3秒过去了，用户没有重连
            # 这是一次“真正”的离开，现在广播消息
            await self.broadcast(room_id, json.dumps({"type": "system", "message": f"{user_name} 离开了课堂。"}))
            print(f"[CHAT] {user_name} (ID: {client_id}) 真正离开了。")
            # 清理临时名字分配
            key = (room_id, client_id)
            if key in self.user_assigned_names:
                # 注意：这里不清理 room_assigned_names，因为可能其他用户使用了相同名字
                # 如果需要精确管理，可以添加使用计数，但鉴于名字池足够大，暂时忽略
                del self.user_assigned_names[key]

        except asyncio.CancelledError:
            # 任务被取消了 (因为用户刷新重连了)
            # 我们什么也不做
            print(f"[CHAT] {user_name} (ID: {client_id}) 的离开消息被取消 (刷新)。")
            pass
        finally:
            # 无论任务是成功执行还是被取消，都从字典中移除
            if client_id in self.disconnect_tasks:
                del self.disconnect_tasks[client_id]

    async def broadcast(self, room_id: int, message: str):
        if room_id in self.rooms:
            for connection in self.rooms[room_id].values():
                await connection.send_text(message)

    async def broadcast_user_list(self, room_id: int):
        if room_id not in self.rooms: return

        user_list = []
        for client_id in self.rooms[room_id].keys():
            user = self.user_info.get(client_id)
            if user:
                user_list.append({"name": user["name"], "role": user["role"]})

        user_list.sort(key=lambda x: (x['role'] != 'teacher', x['name']))
        await self.broadcast(room_id, json.dumps({"type": "user_list", "data": user_list}))


# 创建一个全局实例
manager = MultiRoomConnectionManager()


def get_log_path_for_room(room_id: int) -> Path:
    """根据课堂ID获取日志文件路径 (V4.0 不再依赖 COURSE_INFO)"""
    # 我们可以从数据库中获取班级和课程名，但为了简单，先用ID
    log_filename = f"classroom_{room_id}.log"
    return CHAT_LOG_DIR / log_filename


def normalize_history_message(raw_message: dict) -> Optional[dict]:
    """兼容历史日志中旧消息结构，统一为前端可直接渲染的格式。"""
    if not isinstance(raw_message, dict):
        return None

    msg_type = raw_message.get("type")
    if msg_type in {"chat", "system"}:
        if msg_type == "chat":
            message = dict(raw_message)
            if not message.get("sender"):
                message["sender"] = message.get("user_name", "课堂成员")
            if not message.get("role"):
                message["role"] = message.get("user_role", "student")
            ts = message.get("timestamp")
            if isinstance(ts, str) and "T" in ts and len(ts) >= 16:
                message["timestamp"] = ts[11:16]
            return message
        return raw_message

    # 旧格式聊天消息（无 type 字段）
    if raw_message.get("message") is not None and (raw_message.get("user_name") or raw_message.get("sender")):
        ts = raw_message.get("timestamp") or raw_message.get("logged_at") or ""
        if isinstance(ts, str) and "T" in ts and len(ts) >= 16:
            ts = ts[11:16]
        return {
            "type": "chat",
            "sender": raw_message.get("sender") or raw_message.get("user_name", "课堂成员"),
            "role": raw_message.get("role") or raw_message.get("user_role", "student"),
            "message": raw_message.get("message", ""),
            "timestamp": ts
        }

    # 兜底为系统消息，避免历史丢失
    if raw_message.get("message") is not None:
        return {"type": "system", "message": str(raw_message.get("message", ""))}

    return None


def load_chat_history_for_room(room_id: int):
    """为指定房间加载聊天记录到内存"""
    if room_id in chat_histories: return  # 已经加载

    chat_histories[room_id] = deque(maxlen=MAX_HISTORY_IN_MEMORY)
    log_file = get_log_path_for_room(room_id)

    if log_file.exists():
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        parsed = json.loads(line)
                        normalized = normalize_history_message(parsed)
                        if normalized:
                            chat_histories[room_id].append(normalized)
                    except json.JSONDecodeError:
                        continue
            print(f"[CHAT] 成功加载 {len(chat_histories[room_id])} 条聊天记录 (课堂: {room_id})")
        except Exception as e:
            print(f"[ERROR] 加载聊天记录失败 (课堂: {room_id}): {e}", file=sys.stderr)


async def save_chat_message(room_id: int, message: dict):
    """保存聊天消息到内存和文件"""
    if room_id not in chat_histories:
        load_chat_history_for_room(room_id)  # 确保已初始化

    chat_histories[room_id].append(message)
    log_file = get_log_path_for_room(room_id)

    async with chat_log_lock:
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(message, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"[ERROR] 保存聊天记录失败 (课堂: {room_id}): {e}", file=sys.stderr)
