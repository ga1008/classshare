"""One-shot readback worker; network IO never holds a database transaction."""
from .smart_classroom_write_service import TASK_KIND, reconcile_run


async def handle_smart_attendance_readback(task):
    payload=task["payload"]
    from .smart_classroom_write_service import get_db_connection
    with get_db_connection() as conn:
        row=conn.execute("SELECT state FROM smart_attendance_sync_runs WHERE id=?",(str(payload['run_id']),)).fetchone()
        if row and row['state'] in {'paused','superseded'}:
            return {'run_id':str(payload['run_id']),'state':row['state']}
    result = await reconcile_run({"role":"teacher","id":int(payload["teacher_id"])},str(payload["round_id"]),str(payload["run_id"]))
    return {"run_id":result["id"],"state":result["state"]}


def register_handlers():
    from .scheduled_task_service import register_task_handler
    register_task_handler(TASK_KIND,handle_smart_attendance_readback)
    register_task_handler('smart_attendance_auto_sync',handle_smart_attendance_auto_sync)
    register_task_handler('smart_attendance_student_push',handle_smart_attendance_student_push)


async def handle_smart_attendance_auto_sync(task):
    from .smart_classroom_auto_sync_service import process_auto_sync
    payload=task['payload']
    return await process_auto_sync(int(payload['teacher_id']),str(payload['round_id']))


async def handle_smart_attendance_student_push(task):
    import asyncio
    from .smart_classroom_student_push_service import process
    payload = task['payload']
    return await asyncio.wait_for(process(int(payload['teacher_id']), str(payload['intent_id'])), timeout=90)
