"""Validate a schedule deep link after the caller has authorized the offering."""
from fastapi import HTTPException


def resolve_requested_classroom_session(conn, offering_id: int, raw_session_id: str) -> dict:
    raw = str(raw_session_id or '')
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18 or int(raw) <= 0:
        raise HTTPException(status_code=400, detail='课程链接中的课次编号无效。')
    row = conn.execute('''SELECT id, order_index FROM class_offering_sessions
                          WHERE id = ? AND class_offering_id = ?''',
                       (int(raw), int(offering_id))).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='该课次不属于此课堂或已不存在，请重新打开课表。')
    return dict(row)
