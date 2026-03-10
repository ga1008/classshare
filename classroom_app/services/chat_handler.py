import json
import sys
import asyncio  # 导入 asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from collections import deque
from fastapi import WebSocket

from ..core import chat_histories, chat_log_lock
from ..config import CHAT_LOG_DIR, STUDENT_HISTORY_COUNT, TEACHER_HISTORY_COUNT, MAX_HISTORY_IN_MEMORY
from ..database import get_db_connection

# 刷新去抖的延迟时间 (秒)
REFRESH_DEBOUNCE_SECONDS = 5


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

    def get_room_id(self, websocket: WebSocket) -> int:
        """从 websocket scope 中安全获取 room_id"""
        return int(websocket.path_params.get("class_offering_id", 0))

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
            # 是刷新！获取延迟的“离开”任务
            task = self.disconnect_tasks.pop(client_id)
            # 取消这个任务，这样 "XXX 离开了" 的消息就不会被发送
            task.cancel()
            is_reconnect = True
            print(f"[CHAT] 阻止了 {user['name']} (ID: {client_id}) 的刷新刷屏。")

        if room_id not in self.rooms:
            self.rooms[room_id] = {}
            load_chat_history_for_room(room_id)  # 首次有人进入时，加载历史

        self.rooms[room_id][client_id] = websocket
        self.user_info[client_id] = user
        self.client_to_room[client_id] = room_id

        # 发送历史记录
        history_to_send = self.get_history_for_user(room_id, user)
        if history_to_send:
            await websocket.send_text(json.dumps({"type": "history", "data": history_to_send}))

        # 刷新用户列表是必须的，也是安全的 (不会刷屏)
        await self.broadcast_user_list(room_id)

        # 只有在不是刷新的情况下，才广播“加入了课堂”
        if not is_reconnect:
            await self.broadcast(room_id, json.dumps({"type": "system", "message": f"{user['name']} 加入了课堂。"}))

    def get_history_for_user(self, room_id: int, user: dict) -> List[dict]:
        history_count = TEACHER_HISTORY_COUNT if user['role'] == 'teacher' else STUDENT_HISTORY_COUNT
        room_history = chat_histories.get(room_id, deque())
        return list(room_history)[-history_count:]

    async def disconnect(self, websocket: WebSocket, client_id: str):
        room_id = self.client_to_room.get(client_id, 0)
        if room_id == 0: return

        if client_id in self.rooms[room_id]:
            del self.rooms[room_id][client_id]

        user_name = self.user_info.pop(client_id, {}).get('name', '一位用户')
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
                        chat_histories[room_id].append(json.loads(line))
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