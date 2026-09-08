"""Bounded expiry cleanup for temporary ZIPs; approval snapshots remain auditable."""
from datetime import datetime, timedelta

from ..database import get_db_connection
from .material_signature_service import artifact_path, json_object
from .signature_service import SignatureServiceError


def ensure_material_workflow_cleanup_task(conn):
    from .scheduled_task_service import schedule_task

    return schedule_task(conn, task_kind="material_workflow_cleanup", run_at=datetime.now() + timedelta(minutes=30),
        payload={}, dedupe_key="material-workflows:cleanup", recurrence_seconds=86400,
        title="Expired material ZIP cleanup", priority=95, max_attempts=3, replace=False)


def cleanup_expired_bundles(task=None):
    now, count = datetime.now().isoformat(), 0
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id, artifact_json FROM material_export_bundles WHERE expires_at < ? AND status NOT IN ('running','expired') LIMIT 200", (now,)).fetchall()
        for row in rows:
            artifact = json_object(row["artifact_json"])
            if artifact:
                shared = conn.execute("SELECT id FROM material_export_bundles WHERE id <> ? AND expires_at >= ? AND artifact_json LIKE ? LIMIT 1", (row["id"], now, '%' + artifact["hash"] + '%')).fetchone()
                if not shared:
                    try:
                        artifact_path(artifact).unlink(missing_ok=True)
                    except SignatureServiceError:
                        pass  # Already absent; still expire the reference.
                    except OSError:
                        continue  # An in-flight download may still hold the file.
            conn.execute("UPDATE material_export_bundles SET status = 'expired', artifact_json = '{}', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            count += 1
        conn.commit()
    return f"expired material bundles={count}"
