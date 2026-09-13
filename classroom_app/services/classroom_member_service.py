"""Bounded roster reads independent of cultivation calculation and external sync."""
from __future__ import annotations

import math
from typing import Any

from .offering_membership_service import offering_class_ids


def list_classroom_members(conn, class_offering_id: int, *, q: str = "", class_id: int | None = None,
                           state: str = "", page: int = 1, page_size: int = 50) -> dict[str, Any]:
    class_ids = offering_class_ids(conn, int(class_offering_id))
    size = max(1, min(int(page_size), 100))
    if not class_ids:
        return {"items": [], "total": 0, "student_count": 0, "attention_count": 0,
                "page": 1, "page_size": size, "pages": 1, "classes": []}
    marks = ",".join("?" for _ in class_ids)
    base = f"s.class_id IN ({marks}) AND COALESCE(s.enrollment_status, 'active') = 'active'"
    attention = "EXISTS (SELECT 1 FROM cultivation_alerts a WHERE a.class_offering_id = ? AND a.student_id = s.id AND a.status = 'active')"
    summary = conn.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN {attention} THEN 1 ELSE 0 END),0) AS attention FROM students s WHERE {base}",
                           (int(class_offering_id), *class_ids)).fetchone()
    classes = [dict(row) for row in conn.execute(f"SELECT c.id, c.name, COUNT(s.id) AS student_count FROM classes c JOIN students s ON s.class_id=c.id WHERE {base} GROUP BY c.id,c.name ORDER BY c.name,c.id", tuple(class_ids)).fetchall()]
    params: list[Any] = list(class_ids)
    where = base
    query = str(q or "").strip()[:100]
    if query:
        escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where += " AND (LOWER(s.name) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(s.student_id_number,'')) LIKE ? ESCAPE '\\')"
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    if class_id is not None:
        where += " AND s.class_id = ?"
        params.append(int(class_id))
    if state == "attention":
        where += f" AND {attention}"
        params.append(int(class_offering_id))
    total = int(conn.execute(f"SELECT COUNT(*) AS n FROM students s WHERE {where}", tuple(params)).fetchone()["n"])
    pages = max(1, math.ceil(total / size))
    current = max(1, min(int(page), pages))
    rows = conn.execute(f"""
        SELECT s.id,s.name,s.student_id_number,s.class_id,c.name AS class_name,
               lp.score,lp.progress_percent,lp.dirty AS snapshot_dirty,lp.calculated_at,
               CASE WHEN {attention} THEN 1 ELSE 0 END AS needs_attention
        FROM students s JOIN classes c ON c.id=s.class_id
        LEFT JOIN learning_progress_snapshots lp ON lp.class_offering_id=? AND lp.student_id=s.id
        WHERE {where}
        ORDER BY c.name,c.id,s.student_id_number,s.id LIMIT ? OFFSET ?
    """, (int(class_offering_id), int(class_offering_id), *params, size, (current-1)*size)).fetchall()
    return {"items": [dict(row) for row in rows], "total": total, "student_count": int(summary["n"]),
            "attention_count": int(summary["attention"]), "classes": classes, "page": current,
            "page_size": size, "pages": pages, "applied_filters": {"q": query, "class_id": class_id, "state": state}}
