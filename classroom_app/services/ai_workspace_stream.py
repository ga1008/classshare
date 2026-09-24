"""Keep bounded assistant requests alive across page navigation, not forever."""
import asyncio

from fastapi import HTTPException

MAX_ACTIVE_REQUESTS = 8
_active_tasks = set()


def ensure_capacity():
    if len(_active_tasks) >= MAX_ACTIVE_REQUESTS:
        raise HTTPException(429, 'AI 助手正在处理较多请求，请稍后重试')


def durable_stream(source, encode, *, on_error=None):
    """Producer is owned by the service, consumer only by the HTTP connection.

    No unbounded queue or task fanout: slow/disconnected consumers recover the
    persisted result from history, and the producer still finishes once.
    """
    ensure_capacity()
    queue = asyncio.Queue(maxsize=256)
    connected, overflow = True, False

    async def produce():
        nonlocal connected, overflow
        try:
            async with asyncio.timeout(240):
                async for event in source:
                    if connected:
                        try:
                            queue.put_nowait(event)
                        except asyncio.QueueFull:
                            overflow, connected = True, False
        except Exception:
            if on_error:
                on_error()
            if connected and not queue.full():
                queue.put_nowait(encode('error', message='AI 回复暂时失败，请稍后重试。'))
        finally:
            if connected:
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    overflow, connected = True, False

    task = asyncio.create_task(produce())
    _active_tasks.add(task)

    def completed(done):
        _active_tasks.discard(done)
        # Retrieve an unexpected exception even when the HTTP consumer left.
        if not done.cancelled():
            done.exception()

    task.add_done_callback(completed)

    async def consume():
        nonlocal connected
        try:
            while True:
                if overflow:
                    yield encode('error', message='连接缓慢，请重新打开对话查看完整回复。')
                    break
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            connected = False

    return consume()
