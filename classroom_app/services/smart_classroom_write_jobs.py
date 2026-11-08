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
