"""Reviewed platform operations and a complete mounted-route review ledger.

Route discovery never grants execution. Adding an operation requires reviewing
its normal resource policy, response fields and side effects, including GETs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
from typing import Any

from fastapi import HTTPException


@dataclass(frozen=True)
class ReadOperation:
    key: str
    domain: str
    title: str
    path: str
    endpoint: str
    roles: tuple[str, ...] = ("teacher", "student")
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    side_effects: str = "none"
    fixed_query: dict[str, Any] = field(default_factory=dict)
    requires_super_admin: bool = False

    def public(self) -> dict[str, Any]:
        return {"key": self.key, "domain": self.domain, "title": self.title,
                "method": "GET", "path": self.path, "roles": list(self.roles),
                "parameters": self.parameters, "side_effects": self.side_effects,
                "fixed_query": self.fixed_query, "requires_super_admin": self.requires_super_admin,
                "authorization": "normal_platform_resource_policy", "status": "read_ready"}


def _id(name: str) -> dict[str, dict[str, Any]]:
    return {name: {"in": "path", "type": "integer", "minimum": 1, "maximum": 2**63 - 1, "required": True}}


_PAGE = {"page": {"in": "query", "type": "integer", "minimum": 1, "maximum": 10000},
         "limit": {"in": "query", "type": "integer", "minimum": 1, "maximum": 50}}
_SEARCH = {"q": {"in": "query", "type": "string", "maxLength": 200}}


READ_OPERATIONS = (
    ReadOperation("classroom.learning_weights", "learning", "教师查看课堂学习积分权重", "/api/classrooms/{class_offering_id}/learning/weights",
                  "classroom_app.routers.learning.get_learning_weights", roles=("teacher",), parameters=_id("class_offering_id")),
    ReadOperation("student.score_events", "learning", "本人最近三十天学习积分流水", "/api/classrooms/{class_offering_id}/learning/score-events",
                  "classroom_app.routers.learning.get_learning_score_events", roles=("student",), parameters=_id("class_offering_id")),
    ReadOperation("classroom.grade_publication", "assignments", "教师查看课堂成绩公布状态与历史版本", "/api/classrooms/{class_offering_id}/grade-publication",
                  "classroom_app.routers.materials_parts.final_materials.get_grade_publication_status", roles=("teacher",), parameters=_id("class_offering_id")),
    ReadOperation("classroom.grade_publication_preview", "assignments", "教师核对待公布成绩与来源警告", "/api/classrooms/{class_offering_id}/grade-publication/preview",
                  "classroom_app.routers.materials_parts.final_materials.get_grade_publication_preview", roles=("teacher",),
                  parameters={**_id("class_offering_id"), "material_id": {"in": "query", "type": "integer", "minimum": 1, "required": True}}),
    ReadOperation("classroom.my_courses", "classrooms", "我任教或参与的课堂", "/api/classrooms/mine",
                  "classroom_app.routers.learning.list_my_classrooms",
                  parameters={"limit": {"in": "query", "type": "integer", "minimum": 1, "maximum": 50}, "offset": {"in": "query", "type": "integer", "minimum": 0, "maximum": 10000}}),
    ReadOperation("student.learning_snapshot", "learning", "本人的已就绪学习进度快照与近期变化", "/api/classrooms/{class_offering_id}/learning/snapshot",
                  "classroom_app.routers.learning.get_student_learning_snapshot", roles=("student",), parameters=_id("class_offering_id")),
    ReadOperation("student.report_card", "assignments", "本人的已公布成绩与分类学习成绩单", "/api/report-card",
                  "classroom_app.routers.report_card.api_report_card", roles=("student",),
                  parameters={"class_offering_id": {"in": "query", "type": "integer", "minimum": 1, "required": True},
                              "assessment_kind": {"in": "query", "type": "string", "enum": ["homework", "midterm", "final"]}}),
    ReadOperation("classroom.attendance", "classrooms", "当前权限可见的课堂考勤统计", "/api/classrooms/{class_offering_id}/smart-attendance/analytics",
                  "classroom_app.routers.smart_classroom.api_get_classroom_smart_attendance_analytics", parameters=_id("class_offering_id"), fixed_query={"allow_ai_advice": False}),
    ReadOperation("course.assignment_stats", "assignments", "教师课程内指定课堂的分类作业统计", "/api/courses/{course_id}/assignment-stats",
                  "classroom_app.routers.homework_parts.assignments.get_course_assignment_stats", roles=("teacher",),
                  parameters={**_id("course_id"), "class_offering_id": {"in": "query", "type": "integer", "minimum": 1, "required": True},
                              "semester_id": {"in": "query", "type": "integer", "minimum": 1}, "assessment_kind": {"in": "query", "type": "string", "enum": ["homework", "midterm", "final"]}}),
    ReadOperation("classroom.assignments", "assignments", "当前课堂的作业与考试列表", "/api/classrooms/{class_offering_id}/assignments",
                  "classroom_app.routers.homework_parts.assignments.get_classroom_assignment_list",
                  parameters={**_id("class_offering_id"), "limit": {"in": "query", "type": "integer", "minimum": 1, "maximum": 50},
                              "offset": {"in": "query", "type": "integer", "minimum": 0, "maximum": 10000}}),
    ReadOperation("assignment.details", "assignments", "作业要求、设置与学生本人的提交及已公布成绩", "/api/assignments/{assignment_id}/details",
                  "classroom_app.routers.homework_parts.assignments.get_assignment_details_json",
                  parameters=_id("assignment_id")),
    ReadOperation("search.everything", "search", "搜索我能查看的课堂、材料、作业与博客", "/api/global-search",
                  "classroom_app.routers.global_search.api_global_search",
                  parameters={"q": {"in": "query", "type": "string", "maxLength": 80}}),
    ReadOperation("messages.summary", "messages", "我的消息统计与最近未读通知", "/api/message-center/summary",
                  "classroom_app.routers.message_center.api_message_center_summary",
                  parameters={"include_private": {"in": "query", "type": "boolean"}}),
    ReadOperation("messages.items", "messages", "查看我的通知列表", "/api/message-center/items",
                  "classroom_app.routers.message_center.api_message_center_items",
                  parameters={"category": {"in": "query", "type": "string", "maxLength": 80},
                              "keyword": {"in": "query", "type": "string", "maxLength": 200},
                              "filter": {"in": "query", "type": "string", "enum": ["all", "unread", "normal", "important", "system"]},
                              "limit": {"in": "query", "type": "integer", "minimum": 1, "maximum": 300},
                              "include_private": {"in": "query", "type": "boolean"}}),
    ReadOperation("classroom.contacts", "classrooms", "查看当前课堂可联系的师生", "/api/classrooms/{class_offering_id}/private/contacts",
                  "classroom_app.routers.message_center.api_classroom_private_message_contacts",
                  parameters={"class_offering_id": {"in": "path", "type": "integer", "minimum": 1, "maximum": 2**63 - 1, "required": True}}),
    ReadOperation("assignment.assessment_kind", "assignments", "查看作业考试分类", "/api/assignments/{assignment_id}/assessment-kind",
                  "classroom_app.routers.homework_parts.assignments.get_assignment_assessment_kind", roles=("teacher",),
                  parameters={"assignment_id": {"in": "path", "type": "integer", "minimum": 1, "maximum": 2**63 - 1, "required": True}}),
    ReadOperation("messages.contacts", "messages", "可联系的师生", "/api/message-center/private/contacts",
                  "classroom_app.routers.message_center.api_private_message_contacts"),
    ReadOperation("messages.blocks", "messages", "我的私信屏蔽列表", "/api/message-center/private/blocks",
                  "classroom_app.routers.message_center.api_private_message_blocks"),
    ReadOperation("blog.posts", "blog", "按可见范围浏览博客", "/api/blog/posts", "classroom_app.routers.blog.api_list_posts",
                  parameters={**_PAGE, **_SEARCH, **{key: {"in": "query", "type": "string", "maxLength": 100} for key in ("sort", "author", "tag", "section")}}),
    ReadOperation("blog.mine", "blog", "我的博客与草稿", "/api/blog/my-posts", "classroom_app.routers.blog.api_my_posts",
                  parameters={**_PAGE, "status": {"in": "query", "type": "string", "enum": ["draft", "published", "hidden", "deleted"]}}),
    ReadOperation("blog.comments", "blog", "博客评论", "/api/blog/posts/{post_id}/comments", "classroom_app.routers.blog.api_list_comments",
                  parameters={**_id("post_id"), **_PAGE}),
    ReadOperation("blog.bookmarks", "blog", "我收藏的博客", "/api/blog/bookmarks", "classroom_app.routers.blog.api_bookmarks", parameters=_PAGE),
    ReadOperation("blog.following", "blog", "我关注的博客", "/api/blog/following", "classroom_app.routers.blog.api_blog_following", parameters=_PAGE),
    ReadOperation("blog.follows", "blog", "我的关注设置", "/api/blog/follows", "classroom_app.routers.blog.api_blog_follows"),
    ReadOperation("blog.classes", "blog", "我的博客可选班级", "/api/blog/user-classes", "classroom_app.routers.blog.api_user_classes"),
    ReadOperation("blog.reports", "management", "管理员待处理博客举报", "/api/blog/reports/manage", "classroom_app.routers.blog.api_manage_blog_reports", roles=("teacher",), requires_super_admin=True),
    ReadOperation("classroom.materials", "materials", "查看课堂当前可用学习材料", "/api/classrooms/{class_offering_id}/learning-materials",
                  "classroom_app.routers.materials_parts.learning.list_classroom_learning_materials",
                  parameters={**_id("class_offering_id"), "session_id": {"in": "query", "type": "integer", "minimum": 0}},
                  fixed_query={"generate_blurbs": False}),
    ReadOperation("materials.library", "materials", "我的可见材料库", "/api/materials/library", "classroom_app.routers.materials_parts.library.get_teacher_material_library", roles=("teacher",),
                  parameters={"parent_id": {"in": "query", "type": "integer", "minimum": 1},
                              **{key: {"in": "query", "type": "string", "maxLength": 200} for key in ("keyword", "document_type", "library_view", "scope_level", "school", "department", "college", "course", "class_name", "sort_by", "sort_order")}}),
    ReadOperation("materials.attributes", "materials", "材料属性及当前权限", "/api/materials/{material_id}/attributes", "classroom_app.routers.materials_parts.library.get_material_attributes", roles=("teacher",), parameters=_id("material_id")),
    ReadOperation("class.attributes", "management", "班级属性及当前权限", "/api/manage/classes/{class_id}/attributes", "classroom_app.routers.manage_parts.base_resource_modes.api_get_class_attributes", roles=("teacher",), parameters=_id("class_id")),
    ReadOperation("class.students", "management", "授权班级学生名单", "/api/manage/classes/{class_id}/students", "classroom_app.routers.manage_parts.base_resource_modes.api_get_class_students", roles=("teacher",), parameters=_id("class_id")),
    ReadOperation("course.attributes", "management", "课程属性及当前权限", "/api/manage/courses/{course_id}/attributes", "classroom_app.routers.manage_parts.base_resource_modes.api_get_course_attributes", roles=("teacher",), parameters=_id("course_id")),
    ReadOperation("course.content", "management", "课程教学内容", "/api/manage/courses/{course_id}/content", "classroom_app.routers.manage_parts.base_resource_modes.api_get_course_content", roles=("teacher",), parameters=_id("course_id")),
    ReadOperation("textbook.attributes", "management", "教材属性及当前权限", "/api/manage/textbooks/{textbook_id}/attributes", "classroom_app.routers.manage_parts.base_resource_modes.api_get_textbook_attributes", roles=("teacher",), parameters=_id("textbook_id")),
    ReadOperation("textbook.content", "management", "教材内容", "/api/manage/textbooks/{textbook_id}/content", "classroom_app.routers.manage_parts.base_resource_modes.api_get_textbook_content", roles=("teacher",), parameters=_id("textbook_id")),
    ReadOperation("classroom.mine", "classrooms", "我任教的课堂", "/api/manage/offerings/list", "classroom_app.routers.manage_parts.classes_courses_offerings.api_list_offerings", roles=("teacher",)),
    ReadOperation("organization.tree", "management", "管理员组织树", "/api/manage/system/organizations/tree", "classroom_app.routers.manage_parts.system_config.api_list_organization_tree", roles=("teacher",),
                  parameters={**_SEARCH, "include_inactive": {"in": "query", "type": "integer", "enum": [0, 1]}}, requires_super_admin=True),
    ReadOperation("organization.schools", "management", "管理员学校目录", "/api/manage/system/organizations/schools", "classroom_app.routers.manage_parts.system_config.api_list_organization_school_options", roles=("teacher",),
                  parameters={**_SEARCH, "include_inactive": {"in": "query", "type": "integer", "enum": [0, 1]}}, requires_super_admin=True),
    ReadOperation("session.document_task", "materials", "课时文档生成状态及材料绑定回执", "/api/classrooms/{class_offering_id}/sessions/{session_id}/ai-material-task",
                  "classroom_app.routers.materials_parts.learning.get_classroom_session_ai_material_task", roles=("teacher",),
                  parameters={**_id("class_offering_id"), **_id("session_id")}, fixed_query={"refresh_stale": False}),
    ReadOperation("gongwen.documents", "gongwen", "按组织权限查询公文", "/api/manage/gongwen/documents", "classroom_app.routers.manage_parts.integrations.api_list_gongwen_documents", roles=("teacher",),
                  parameters={**{key: {"in": "query", "type": "string", "maxLength": 200} for key in ("keyword", "category", "author", "sender", "parse_status")},
                              **{key: {"in": "query", "type": "integer", "enum": [0, 1]} for key in ("has_attachment", "unread", "favorite", "follow", "with_facets")},
                              "limit": {"in": "query", "type": "integer", "minimum": 1, "maximum": 100}, "offset": {"in": "query", "type": "integer", "minimum": 0, "maximum": 10000}}),
    ReadOperation("gongwen.search", "gongwen", "检索可见公文正文", "/api/manage/gongwen/documents/search", "classroom_app.routers.manage_parts.integrations.api_search_gongwen_documents", roles=("teacher",),
                  parameters={**_SEARCH, "category": {"in": "query", "type": "string", "maxLength": 200}, "limit": {"in": "query", "type": "integer", "minimum": 1, "maximum": 100}}),
)
READ_OPERATION_BY_KEY = {item.key: item for item in READ_OPERATIONS}

# These endpoints are GETs with real business effects. They must be adapted
# with the operation ledger before execution, not relabeled as pure reads.
SIDE_EFFECT_GET_PATHS = {
    "/api/classrooms/{class_offering_id}/smart-attendance/analytics",
    "/api/classrooms/{class_offering_id}/learning/progress",
    "/message-center/notifications/{notification_id}/open",
    "/api/message-center/private/conversation",
    "/api/learning/personal-greeting",
    "/api/learning/cultivation-profile",
    "/api/assignments/time-state",
    "/api/blog/posts/{post_id}",
    "/api/blog/discovery",
    "/api/classrooms/{class_offering_id}/learning-materials",
    "/api/classrooms/{class_offering_id}/sessions/{session_id}/ai-material-task",
    "/api/manage/gongwen/documents/{document_id}/reader",
}
_AUTHENTICATION_PATH_PARTS = {"auth", "login", "logout", "register"}


def mounted_routes(app):
    """Yield all mounted methods, including hidden and non-OpenAPI surfaces."""
    def walk(routes, prefix=""):
        for route in routes:
            path = prefix + str(getattr(route, "path", ""))
            children = getattr(route, "routes", None)
            if children is None:
                children = getattr(getattr(route, "app", None), "routes", None)
            if children:
                yield from walk(children, path)
                continue
            endpoint = getattr(route, "endpoint", None)
            handler = (getattr(endpoint, "__module__", "") + "." + getattr(endpoint, "__qualname__", "")).strip(".")
            methods = getattr(route, "methods", None) or ("WEBSOCKET" if "WebSocket" in type(route).__name__ else "MOUNT",)
            for method in sorted(methods):
                yield {"method": method, "path": path, "handler": handler, "route": route}
    yield from walk(getattr(app, "routes", ()))


def _domain(row: dict) -> str:
    module = row["handler"].rsplit(".", 1)[0]
    for package, domain in (("homework_parts", "assignments"), ("materials_parts", "materials"),
                            ("manage_parts", "management"), ("ui_parts", "pages"), (".mp.", "miniapp")):
        if package in module:
            return domain
    return module.rsplit(".", 1)[-1] or "application"


def _exclusion(row: dict) -> str | None:
    path = row["path"]
    segments = set(path.lower().strip("/").split("/"))
    if segments & _AUTHENTICATION_PATH_PARTS:
        return "authentication_bootstrap_or_session_transition"
    if path.startswith(("/api/agent-bridge", "/api/internal/")):
        return "internal_tool_transport_or_recursive_agent"
    if row["method"] == "POST" and (path == "/api/agent-tasks" or path.endswith(("/follow-up", "/retry")) and path.startswith("/api/agent-tasks/")):
        return "recursive_agent_task_execution"
    return None


def _special_review_reason(row: dict) -> str | None:
    text = (row["path"] + " " + row["handler"]).lower()
    if any(word in text for word in ("password", "credential", "secret", "api-key", "api_key", "agent_key", "email_config")):
        return "special_secure_input_required"
    if "agent" in text or "system_monitor" in text:
        return "control_plane_review_required"
    return None


def build_platform_route_inventory(app) -> list[dict[str, Any]]:
    expected = {(item.path, item.endpoint): item for item in READ_OPERATIONS}
    rows = list(mounted_routes(app))
    collisions = Counter((row["method"], row["path"]) for row in rows)
    inventory = []
    for row in rows:
        operation = expected.get((row["path"], row["handler"])) if row["method"] == "GET" else None
        exclusion = _exclusion(row)
        status = "excluded" if exclusion else "needs_adapter"
        reason = exclusion or _special_review_reason(row) or ("side_effect_get" if row["path"] in SIDE_EFFECT_GET_PATHS else "domain_policy_and_side_effect_review_required")
        if collisions[(row["method"], row["path"])] > 1:
            status, reason = "needs_adapter", "duplicate_mounted_method_and_path"
        elif operation and not exclusion:
            status, reason = "read_ready", "reviewed_fixed_no_side_effect_query" if operation.fixed_query else "reviewed_normal_platform_handler"
        inventory.append({"operation_key": operation.key if operation else "route." + hashlib.sha256((row["method"] + "\n" + row["path"]).encode()).hexdigest()[:20],
                          "method": row["method"], "path": row["path"], "handler": row["handler"],
                          "domain": operation.domain if operation else _domain(row), "status": status, "reason": reason})
    return inventory


def platform_read_catalog(app, *, actor_role: str, is_super_admin: bool = False) -> dict[str, Any]:
    if actor_role not in {"teacher", "student"}:
        raise HTTPException(403, "Agent 平台身份无效。")
    inventory = build_platform_route_inventory(app)
    ready = {row["operation_key"] for row in inventory if row["status"] == "read_ready"}
    domains = defaultdict(Counter)
    for row in inventory:
        domains[row["domain"]][row["status"]] += 1
    return {"operations": [item.public() for item in READ_OPERATIONS if item.key in ready and actor_role in item.roles and (not item.requires_super_admin or is_super_admin)],
            "domains": [{"domain": name, **dict(counts)} for name, counts in sorted(domains.items())],
            "mounted_operation_count": len(inventory), "coverage_status": "partial_reviewed_reads",
            "note": "未审查路由和带副作用的读取尚需领域适配；目录可见性不替代资源权限。"}


def resolve_read_operation(app, key: str, actor_role: str) -> ReadOperation:
    item = READ_OPERATION_BY_KEY.get(key)
    if item is None or actor_role not in item.roles:
        raise HTTPException(403, "当前身份没有此已审核平台读取能力。")
    rows = [row for row in build_platform_route_inventory(app) if row["operation_key"] == key]
    if len(rows) != 1 or rows[0]["status"] != "read_ready":
        raise HTTPException(503, "平台读取路由尚未挂载或已变化，需重新审核。")
    return item
