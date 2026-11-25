"""Reserve before native HTTP dispatch; keep uncertain mutations reserved."""
from fastapi import HTTPException


def action_resources(operation):
    # Deliveries charge in the message transaction, including native replays.
    delivery_paths = {"/api/social/conversations/{key}/messages",
                      "/api/social/conversations/{key}/nudge", "/api/message-center/private/messages"}
    if not operation.mutates or operation.method == "GET" or (operation.method == "POST" and operation.path in delivery_paths):
        return []
    resources = ["active_interactions"]
    if operation.method == "POST" and operation.path == "/api/blog/posts":
        resources.append("blog_posts")
    if operation.method == "POST" and operation.path == "/api/blog/posts/{post_id}/comments":
        resources.append("comments")
    return resources


def _key(wanderer_id, operation_id, resource):
    return f"platform-action:{wanderer_id}:{operation_id}:{resource}"


def reserve_action_resources(conn, source, operation, operation_id):
    from .wanderer_resource_service import reserve
    for resource in action_resources(operation):
        result = reserve(conn, source.wanderer_id, resource, 1, _key(source.wanderer_id, operation_id, resource))
        if not result.get("ok"):
            raise HTTPException(429, {"message": "散修动作资源不足，指令未执行。", "resource": resource, "snapshot": result.get("snapshot")})


def settle_action_resources(conn, source, operation, operation_id, status):
    if status not in {"succeeded", "failed", "submitted"}:
        return  # No HTTP receipt is evidence that an uncertain write did not occur.
    from .wanderer_resource_service import settle
    for resource in action_resources(operation):
        key = _key(source.wanderer_id, operation_id, resource)
        if operation.path.startswith(("/api/social/", "/api/message-center/private/")):
            # Jobs accepted before social writes joined this budget can still be reconciled.
            if not conn.execute("SELECT 1 FROM wanderer_resource_reservations WHERE operation_id=?", (key,)).fetchone():
                continue
        settle(conn, key, release=status == "failed")
