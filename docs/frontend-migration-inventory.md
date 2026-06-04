# LanShare 前端迁移清单

> 生成时间：2026-06-04 15:56:56（Asia/Shanghai）。
> 生成命令：`python tools/frontend_migration_inventory.py`。

## 用途

这个清单是前端现代化迁移的功能防漏基线。迁移任何页面前，必须先确认对应模板、脚本、路由和业务入口已经在这里被识别；迁移后再用它对照检查入口是否消失、脚本是否重复、Vite island 是否按页面加载。

## 当前 Vite Islands

| 入口 | 产物 | 共享依赖 |
| --- | --- | --- |
| `frontend/src/islands/app-shell.tsx` | `assets/app-shell-BCV5633m.js` | `_mount-react-island-DEL8WbNn.js` |
| `frontend/src/islands/assignment-authoring-sync.tsx` | `assets/assignment-authoring-sync-CV-GO8kC.js` | `_mount-react-island-DEL8WbNn.js`, `_createLucideIcon-u9LDZDWz.js`, `_bell-_fFZyebH.js`, `_calendar-clock-DdkMlaDE.js` 等 7 项 |
| `frontend/src/islands/assignment-submit-sync.tsx` | `assets/assignment-submit-sync-CVd3TaAx.js` | `_mount-react-island-DEL8WbNn.js`, `_island-payload-DC_mu5iS.js`, `_createLucideIcon-u9LDZDWz.js`, `_circle-alert-CvvlF6cH.js` 等 6 项 |
| `frontend/src/islands/assignment-task-board-sync.tsx` | `assets/assignment-task-board-sync-DoX3eJVW.js` | `_mount-react-island-DEL8WbNn.js`, `_createLucideIcon-u9LDZDWz.js`, `_book-open-check-D0cVMpRi.js`, `_circle-alert-CvvlF6cH.js` 等 8 项 |
| `frontend/src/islands/blog-launcher.tsx` | `assets/blog-launcher-CnfmV2gE.js` | `_mount-react-island-DEL8WbNn.js`, `_action-entry-BmfcB9Zw.js`, `_createLucideIcon-u9LDZDWz.js` |
| `frontend/src/islands/blog-topbar-sync.tsx` | `assets/blog-topbar-sync-CFAWVYG3.js` | `_mount-react-island-DEL8WbNn.js` |
| `frontend/src/islands/classroom-activity-workspace-sync.tsx` | `assets/classroom-activity-workspace-sync-IEikDpKm.js` | `_mount-react-island-DEL8WbNn.js` |
| `frontend/src/islands/classroom-page.tsx` | `assets/classroom-page-CSlxw65z.js` | `_mount-react-island-DEL8WbNn.js`, `_preload-helper-zJ_50EbN.js` |
| `frontend/src/islands/dashboard-quick-actions.tsx` | `assets/dashboard-quick-actions-B8oXcBlZ.js` | `_mount-react-island-DEL8WbNn.js`, `_island-payload-DC_mu5iS.js` |
| `frontend/src/islands/exam-assign-sync.tsx` | `assets/exam-assign-sync-Z98ifKgX.js` | `_mount-react-island-DEL8WbNn.js`, `_bell-_fFZyebH.js`, `_book-open-check-D0cVMpRi.js`, `_calendar-clock-DdkMlaDE.js` 等 9 项 |
| `frontend/src/islands/feedback-launcher.tsx` | `assets/feedback-launcher-D21R5-KX.js` | `_mount-react-island-DEL8WbNn.js`, `_action-entry-BmfcB9Zw.js`, `_circle-alert-CvvlF6cH.js` |
| `frontend/src/islands/learning-progress-sync.tsx` | `assets/learning-progress-sync-DREGzc1N.js` | `_mount-react-island-DEL8WbNn.js`, `_book-open-check-D0cVMpRi.js`, `_circle-alert-CvvlF6cH.js`, `_circle-check-BpXIs8GZ.js` 等 8 项 |
| `frontend/src/islands/material-learning-path-sync.tsx` | `assets/material-learning-path-sync-Ou0x5UxS.js` | `_mount-react-island-DEL8WbNn.js`, `_book-open-check-D0cVMpRi.js`, `_calendar-clock-DdkMlaDE.js`, `_circle-alert-CvvlF6cH.js` 等 10 项 |
| `frontend/src/islands/materials-manage-page.tsx` | `assets/materials-manage-page-C9GDayMX.js` | `_mount-react-island-DEL8WbNn.js`, `_preload-helper-zJ_50EbN.js` |
| `frontend/src/islands/message-center-page.tsx` | `assets/message-center-page-DhfWpPPR.js` | `_mount-react-island-DEL8WbNn.js`, `_preload-helper-zJ_50EbN.js` |
| `frontend/src/islands/message-center-sync.tsx` | `assets/message-center-sync-C2wczGk6.js` | `_mount-react-island-DEL8WbNn.js` |
| `frontend/src/islands/message-center-workspace-sync.tsx` | `assets/message-center-workspace-sync-D9sPvr_a.js` | `_mount-react-island-DEL8WbNn.js`, `_createLucideIcon-u9LDZDWz.js`, `_bell-_fFZyebH.js`, `_refresh-cw-BbXzb7Yt.js` 等 5 项 |
| `frontend/src/islands/profile-launcher.tsx` | `assets/profile-launcher-DNrTF52w.js` | `_mount-react-island-DEL8WbNn.js`, `_action-entry-BmfcB9Zw.js` |
| `frontend/src/islands/resource-workspace-sync.tsx` | `assets/resource-workspace-sync-2pz43c_I.js` | `_mount-react-island-DEL8WbNn.js`, `_createLucideIcon-u9LDZDWz.js`, `_file-text-CPrph7wP.js`, `_refresh-cw-BbXzb7Yt.js` |
| `frontend/src/islands/student-security-sync.tsx` | `assets/student-security-sync-BPDieo3E.js` | `_mount-react-island-DEL8WbNn.js` |
| `frontend/src/islands/submission-jump-nav.tsx` | `assets/submission-jump-nav-Gm3OVmLR.js` | `_mount-react-island-DEL8WbNn.js`, `_island-payload-DC_mu5iS.js`, `_createLucideIcon-u9LDZDWz.js`, `_circle-check-BpXIs8GZ.js` |

## 业务域覆盖图

| 业务域 | 模板数量 | 关键模板 | 关键脚本线索 |
| --- | ---: | --- | --- |
| 作业与提交 | 4 | `assignment_detail_student.html`, `assignment_detail_teacher.html`, `assignment_wrong_summary.html`, `submission_detail.html` | `js/ui.js`, `js/submission_upload.js`, `js/assignment_time.js`, `js/review_docx_export.js`, `js/behavior_tracker.js`, `js/feedback.js` 等 13 项 |
| 其他页面 | 13 | `base.html`, `base_centered.html`, `base_navbar.html`, `error.html`, `macros/app_topbar.html`, `partials/ai_workspace_widget.html` | `js/auth.js`, `js/cultivation_identity.js`, `js/message_center_bell.js`, `js/teacher_onboarding.js`, `js/ui.js`, `js/ai_chat_component.js` 等 9 项 |
| 教师管理中心 | 11 | `manage/ai.html`, `manage/classes.html`, `manage/classrooms.html`, `manage/courses.html`, `manage/layout.html`, `manage/offerings.html` | `js/manage_ai.js`, `js/manage_classes.js`, `js/manage_classrooms.js`, `js/manage_courses.js`, `js/ai_chat_component.js`, `js/ai_workspace_widget.js` 等 17 项 |
| 消息、反馈、博客、个人中心 | 9 | `blog.html`, `feedback_review.html`, `message_center.html`, `partials/blog_button.html`, `partials/feedback_button.html`, `partials/feedback_modal.html` | `js/blog.js`, `js/feedback_review.js`, `js/echarts.min.js`, `js/profile.js` |
| 系统与超管 | 11 | `manage/system/academic_integrations.html`, `manage/system/agent_keys.html`, `manage/system/blog_crawler.html`, `manage/system/diagnostics.html`, `manage/system/feedback.html`, `manage/system/organizations.html` | `js/manage_academic_integrations.js`, `js/manage_agent_keys.js`, `js/manage_blog_crawler.js`, `js/manage_organizations.js`, `js/manage_smart_classroom_integrations.js` |
| 考试与试卷 | 3 | `exam_editor.html`, `exam_take.html`, `manage/exams.html` | `js/exam_paper_preview.js`, `js/auth.js`, `js/ui.js`, `js/grading_feedback.js`, `js/behavior_tracker.js`, `js/tools.js` 等 12 项 |
| 认证与账号 | 6 | `partials/student_security_modal.html`, `permission_denied.html`, `session_expired.html`, `student_login_v4.html`, `teacher_login_v4.html`, `teacher_register_v4.html` | `js/ui.js`, `js/student_login.js` |
| 资料与文件 | 4 | `manage/materials.html`, `material_viewer.html`, `partials/learning_material_selector.html`, `partials/session_material_ai_modal.html` | `js/teacher_whiteboard.js`, `js/material_viewer.js`, `js/ai_chat_component.js`, `js/ai_workspace_widget.js` |
| 首页与课堂 | 4 | `classroom_main_v4.html`, `dashboard.html`, `learning_path.html`, `partials/semester_calendar_panel.html` | `js/tools.js`, `js/ai_workspace_widget.js`, `js/behavior_tracker.js`, `js/feedback.js`, `js/student_security.js`, `js/ai_chat_component.js` 等 8 项 |

## 模板清单

| 模板 | 继承/引用 | 静态脚本与样式 | Vite 入口 | Island |
| --- | --- | --- | --- | --- |
| `assignment_detail_student.html` | `base.html`, `partials/message_center_bell.html`, `partials/feedback_button.html`, `partials/profile_button.html` 等 6 项 | `js/ui.js`, `js/submission_upload.js`, `js/assignment_time.js`, `js/review_docx_export.js`, `js/behavior_tracker.js` 等 7 项 | `frontend/src/islands/assignment-submit-sync.tsx` | `assignment-submit-sync` |
| `assignment_detail_teacher.html` | `base.html`, `partials/feedback_modal.html`, `partials/ai_workspace_widget.html`, `partials/markdown_assets.html` 等 5 项 | `css/exam_paper_preview.css`, `js/echarts.min.js`, `js/ai_chat_component.js`, `js/ai_workspace_widget.js`, `js/ui.js` 等 8 项 | - | - |
| `assignment_wrong_summary.html` | `base.html`, `partials/markdown_assets.html`, `macros/app_topbar.html` | `js/grading_feedback.js` | - | - |
| `base.html` | `partials/ui_system_assets.html`, `partials/vite_islands.html` | `js/auth.js`, `js/cultivation_identity.js`, `js/message_center_bell.js`, `js/teacher_onboarding.js` | - | - |
| `base_centered.html` | `base.html`, `partials/site_record_footer.html` | `js/ui.js` | - | - |
| `base_navbar.html` | `base.html`, `partials/app_topbar_utility_actions.html`, `partials/student_security_modal.html`, `partials/feedback_modal.html` 等 7 项 | `js/ai_chat_component.js`, `js/ai_workspace_widget.js`, `js/ui.js`, `js/student_security.js`, `js/feedback.js` | - | - |
| `blog.html` | `base_navbar.html`, `partials/markdown_assets.html` | `js/blog.js` | - | - |
| `classroom_main_v4.html` | `base.html`, `partials/markdown_assets.html`, `partials/student_security_modal.html`, `partials/feedback_modal.html` 等 8 项 | `js/tools.js`, `js/ai_workspace_widget.js`, `js/behavior_tracker.js`, `js/feedback.js`, `js/student_security.js` 等 6 项 | `frontend/src/islands/classroom-page.tsx`, `frontend/src/islands/assignment-task-board-sync.tsx`, `frontend/src/islands/classroom-activity-workspace-sync.tsx` 等 8 项 | `classroom-page`, `learning-progress-sync`, `assignment-task-board-sync` 等 8 项 |
| `dashboard.html` | `base_navbar.html`, `partials/semester_calendar_panel.html` | `js/dashboard.js` | `frontend/src/islands/dashboard-quick-actions.tsx` | `dashboard-quick-actions` |
| `error.html` | `base_centered.html` | - | - | - |
| `exam_editor.html` | `partials/ui_system_assets.html`, `partials/markdown_assets.html` | `css/exam_paper_preview.css`, `js/exam_paper_preview.js`, `js/auth.js`, `js/ui.js`, `js/grading_feedback.js` | - | - |
| `exam_take.html` | `partials/ui_system_assets.html`, `partials/markdown_assets.html`, `macros/app_topbar.html` | `js/auth.js`, `js/behavior_tracker.js`, `js/tools.js`, `js/ui.js`, `js/api.js` 等 11 项 | - | - |
| `feedback_review.html` | `base_navbar.html` | `js/feedback_review.js` | - | - |
| `learning_path.html` | `base_navbar.html` | `js/learning_path.js` | - | - |
| `macros/app_topbar.html` | - | - | - | - |
| `manage/ai.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_ai.js` | - | - |
| `manage/classes.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_classes.js` | - | - |
| `manage/classrooms.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_classrooms.js` | - | - |
| `manage/courses.html` | `manage/layout.html`, `partials/learning_material_selector.html`, `macros/app_topbar.html` | `js/manage_courses.js` | - | - |
| `manage/exams.html` | `manage/layout.html`, `macros/app_topbar.html` | `css/exam_paper_preview.css`, `js/api.js`, `js/ui.js`, `js/exam_paper_preview.js` | - | - |
| `manage/layout.html` | `partials/ui_system_assets.html`, `partials/ai_workspace_widget.html`, `partials/vite_islands.html`, `partials/feedback_modal.html` 等 7 项 | `js/ai_chat_component.js`, `js/ai_workspace_widget.js`, `js/auth.js`, `js/message_center_bell.js`, `js/ui.js` 等 8 项 | - | - |
| `manage/materials.html` | `manage/layout.html`, `macros/app_topbar.html` | - | `frontend/src/islands/materials-manage-page.tsx` | `materials-manage-page` |
| `manage/offerings.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_offerings.js` | - | - |
| `manage/semesters.html` | `manage/layout.html`, `partials/semester_calendar_panel.html`, `macros/app_topbar.html` | `js/manage_semesters.js` | - | - |
| `manage/signatures.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_signatures.js` | - | - |
| `manage/student_detail.html` | `manage/layout.html`, `macros/app_topbar.html` | - | - | - |
| `manage/system/academic_integrations.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_academic_integrations.js` | - | - |
| `manage/system/agent_keys.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_agent_keys.js` | - | - |
| `manage/system/blog_crawler.html` | `manage/layout.html` | `js/manage_blog_crawler.js` | - | - |
| `manage/system/diagnostics.html` | `manage/layout.html` | - | - | - |
| `manage/system/feedback.html` | `manage/layout.html` | - | - | - |
| `manage/system/organizations.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_organizations.js` | - | - |
| `manage/system/password_resets.html` | `manage/layout.html`, `macros/app_topbar.html` | - | - | - |
| `manage/system/smart_classroom_integrations.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_smart_classroom_integrations.js` | - | - |
| `manage/system/super_admin.html` | `manage/layout.html` | - | - | - |
| `manage/system/users.html` | `manage/layout.html`, `macros/app_topbar.html` | - | - | - |
| `manage/system.html` | `manage/layout.html` | - | - | - |
| `manage/textbooks.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_textbooks.js` | - | - |
| `manage/workflow.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/manage_workflow.js` | - | - |
| `material_viewer.html` | `base.html`, `partials/markdown_assets.html`, `partials/ai_workspace_widget.html` | `js/teacher_whiteboard.js`, `js/material_viewer.js`, `js/ai_chat_component.js`, `js/ai_workspace_widget.js` | - | - |
| `message_center.html` | `base_navbar.html`, `partials/markdown_assets.html` | - | `frontend/src/islands/message-center-page.tsx`, `frontend/src/islands/message-center-workspace-sync.tsx` | `message-center-page`, `message-center-workspace-sync` |
| `partials/ai_workspace_widget.html` | - | - | - | - |
| `partials/app_topbar_utility_actions.html` | `macros/app_topbar.html` | - | - | - |
| `partials/blog_button.html` | - | - | `frontend/src/islands/blog-launcher.tsx` | `blog-launcher` |
| `partials/feedback_button.html` | - | - | `frontend/src/islands/feedback-launcher.tsx` | `feedback-launcher` |
| `partials/feedback_modal.html` | - | - | - | - |
| `partials/learning_material_selector.html` | - | - | - | - |
| `partials/markdown_assets.html` | - | `es2022_polyfills`, `marked`, `mermaid`, `markdown_runtime` | - | - |
| `partials/message_center_bell.html` | - | - | - | - |
| `partials/profile_button.html` | - | - | `frontend/src/islands/profile-launcher.tsx` | `profile-launcher` |
| `partials/semester_calendar_panel.html` | - | - | - | - |
| `partials/session_material_ai_modal.html` | - | - | - | - |
| `partials/site_record_footer.html` | - | - | - | - |
| `partials/student_security_modal.html` | - | - | - | - |
| `partials/teacher_onboarding_modal.html` | - | - | - | - |
| `partials/ui_system_assets.html` | - | `tailwind_app` | - | - |
| `partials/vite_islands.html` | - | - | `frontend/src/islands/app-shell.tsx`, `frontend/src/islands/message-center-sync.tsx`, `frontend/src/islands/blog-topbar-sync.tsx` 等 4 项 | `app-shell`, `message-center-sync`, `blog-topbar-sync` 等 4 项 |
| `permission_denied.html` | `base_centered.html` | - | - | - |
| `profile.html` | `base_navbar.html`, `partials/markdown_assets.html` | `js/echarts.min.js`, `js/profile.js` | `frontend/src/islands/message-center-page.tsx`, `frontend/src/islands/message-center-workspace-sync.tsx` | `message-center-page`, `message-center-workspace-sync` |
| `session_expired.html` | `base_centered.html` | `js/ui.js` | - | - |
| `status.html` | `base_centered.html` | - | - | - |
| `student_login_v4.html` | `base_centered.html` | `js/student_login.js` | - | - |
| `submission_detail.html` | `base.html`, `partials/message_center_bell.html`, `partials/feedback_button.html`, `partials/profile_button.html` 等 6 项 | `js/api.js`, `js/file_preview.js`, `js/ui.js`, `js/submission_upload.js`, `js/feedback.js` 等 6 项 | `frontend/src/islands/submission-jump-nav.tsx` | `submission-jump-nav` |
| `teacher_login_v4.html` | `base_centered.html` | - | - | - |
| `teacher_register_v4.html` | `base_centered.html` | - | - | - |

## 路由与模板线索

| 路由文件 | 路由数量 | 路由样例 | 模板线索 |
| --- | ---: | --- | --- |
| `classroom_app/routers/agent_tasks.py` | 6 | `GET /bootstrap`, `POST /composer`, `DELETE /history`, `GET /{task_id}` 等 6 项 | - |
| `classroom_app/routers/ai.py` | 17 | `POST /ai/generate_assignment`, `POST /submissions/{submission_id}/regrade`, `POST /submissions/{submission_id}/force-regrade`, `POST /submissions/{submission_id}/stop-grading` 等 8 项 | - |
| `classroom_app/routers/behavior.py` | 1 | `POST /{class_offering_id}/behavior/batch` | - |
| `classroom_app/routers/blog.py` | 25 | `GET /blog`, `GET /api/blog/posts`, `GET /api/blog/summary`, `GET /api/blog/discovery` 等 8 项 | `blog.html` |
| `classroom_app/routers/classroom_interactions.py` | 9 | `GET /classrooms/{class_offering_id}/snapshot`, `POST /classrooms/{class_offering_id}/activities`, `POST /activities/{activity_id}/respond`, `POST /activities/{activity_id}/questions` 等 8 项 | - |
| `classroom_app/routers/collaboration.py` | 12 | `GET /classrooms/{class_offering_id}/snapshot`, `POST /classrooms/{class_offering_id}/groups`, `PUT /groups/{group_id}`, `POST /groups/{group_id}/join` 等 8 项 | - |
| `classroom_app/routers/emoji.py` | 3 | `GET /api/classrooms/{class_offering_id}/emoji-panel`, `POST /api/classrooms/{class_offering_id}/custom-emojis`, `GET /api/classrooms/{class_offering_id}/custom-emojis/{emoji_id}/file` | - |
| `classroom_app/routers/feedback.py` | 6 | `POST /api/feedback`, `POST /api/feedback/{feedback_id}/upload`, `GET /api/feedback/{feedback_id}/attachment/{file_hash}`, `GET /api/feedback/my` 等 6 项 | - |
| `classroom_app/routers/files.py` | 19 | `POST /api/files/check`, `POST /api/files/upload/init`, `POST /api/files/upload/chunk`, `POST /api/files/upload/complete` 等 8 项 | - |
| `classroom_app/routers/homework_parts/assignments.py` | 5 | `POST /courses/{course_id}/assignments`, `PUT /assignments/{assignment_id}`, `DELETE /assignments/{assignment_id}`, `GET /assignments/time-state` 等 5 项 | - |
| `classroom_app/routers/homework_parts/drafts.py` | 3 | `GET /assignments/{assignment_id}/draft`, `POST /assignments/{assignment_id}/draft`, `GET /assignments/{assignment_id}/draft-files/{file_id}` | - |
| `classroom_app/routers/homework_parts/exam_papers.py` | 9 | `GET /exam-papers`, `PUT /exam-papers/{paper_id}/tags`, `GET /exam-papers/json-template`, `POST /exam-papers/import-json` 等 8 项 | - |
| `classroom_app/routers/homework_parts/exports.py` | 3 | `GET /assignments/{assignment_id}/export-attachments/{class_offering_id}`, `GET /assignments/{assignment_id}/export/{class_offering_id}`, `GET /assignments/{assignment_id}/export-review-docx` | - |
| `classroom_app/routers/homework_parts/grading.py` | 3 | `POST /assignments/{assignment_id}/submissions/zero-unsubmitted`, `POST /submissions/{submission_id}/grade`, `POST /assignments/{assignment_id}/submissions/batch-grade` | - |
| `classroom_app/routers/homework_parts/submissions.py` | 8 | `GET /assignments/{assignment_id}/submissions`, `DELETE /submissions/{submission_id}`, `POST /submissions/{submission_id}/files`, `DELETE /submission-files/{file_id}` 等 8 项 | - |
| `classroom_app/routers/learning.py` | 9 | `GET /learning/cultivation-profile`, `GET /classrooms/{class_offering_id}/learning/progress`, `GET /classrooms/{class_offering_id}/todos`, `POST /classrooms/{class_offering_id}/todos` 等 8 项 | - |
| `classroom_app/routers/learning_path.py` | 3 | `GET /learning-path`, `GET /api/learning-path/bootstrap`, `POST /api/learning-path/items` | `learning_path.html` |
| `classroom_app/routers/manage_parts/classes_courses_classes.py` | 16 | `POST /classes/create`, `POST /classes/sync-current-academic`, `GET /classrooms/teaching-places`, `POST /classrooms/sync-academic` 等 8 项 | - |
| `classroom_app/routers/manage_parts/classes_courses_courses.py` | 6 | `POST /courses/save`, `POST /courses/sync-current-academic`, `POST /courses/ai-generate-lessons`, `POST /courses/create` 等 6 项 | - |
| `classroom_app/routers/manage_parts/classes_courses_offerings.py` | 8 | `POST /class_offerings/preview`, `POST /class_offerings/save`, `POST /class_offerings/create`, `DELETE /class_offerings/{offering_id}` 等 8 项 | - |
| `classroom_app/routers/manage_parts/classes_courses_onboarding.py` | 5 | `GET /teacher-onboarding/state`, `POST /teacher-onboarding/dismiss`, `POST /teacher-onboarding/classes/create`, `POST /teacher-onboarding/course-description` 等 5 项 | - |
| `classroom_app/routers/manage_parts/integrations.py` | 15 | `GET /system/academic-credentials`, `GET /system/academic-sync-capabilities`, `POST /system/academic-sync`, `POST /system/integration-request-probe` 等 8 项 | - |
| `classroom_app/routers/manage_parts/semesters_textbooks.py` | 8 | `POST /semesters/save`, `POST /semesters/{semester_id}/calendar/sync`, `POST /semesters/calendar/sync-current`, `DELETE /semesters/{semester_id}` 等 8 项 | - |
| `classroom_app/routers/manage_parts/system_config.py` | 36 | `GET /system/background-tasks`, `GET /system/password-resets/{request_id}`, `POST /system/super-admin`, `GET /system/organizations/tree` 等 8 项 | - |
| `classroom_app/routers/materials_parts/ai_import.py` | 10 | `GET /api/materials/ai-generation/candidates`, `GET /api/materials/ai-generation/assignments`, `POST /api/materials/ai-generate`, `POST /api/materials/ai-import` 等 8 项 | - |
| `classroom_app/routers/materials_parts/exports.py` | 6 | `GET /api/materials/ai-import-records/{record_id}/export`, `GET /api/materials/{material_id}/ai-import/export`, `GET /materials/view/{material_id}`, `GET /materials/raw/{material_id}` 等 6 项 | `material_viewer.html` |
| `classroom_app/routers/materials_parts/final_materials.py` | 1 | `POST /api/classrooms/{class_offering_id}/final-materials/generate` | - |
| `classroom_app/routers/materials_parts/learning.py` | 6 | `POST /api/materials/{material_id}/ai-assign-sessions`, `PUT /api/classrooms/{class_offering_id}/learning-home-material`, `PUT /api/classrooms/{class_offering_id}/sessions/{session_id}/learning-material`, `GET /api/classrooms/{class_offering_id}/sessions/{session_id}/ai-material-task` 等 6 项 | - |
| `classroom_app/routers/materials_parts/library.py` | 14 | `GET /manage/materials`, `GET /api/materials/library`, `GET /api/materials/{material_id}`, `GET /api/materials/{material_id}/repository` 等 8 项 | `manage/materials.html` |
| `classroom_app/routers/message_center.py` | 16 | `GET /message-center`, `GET /api/message-center/bootstrap`, `GET /api/message-center/summary`, `GET /api/message-center/items` 等 8 项 | - |
| `classroom_app/routers/profile.py` | 16 | `GET /profile`, `GET /api/profile/bootstrap`, `GET /api/profile/portfolio`, `POST /api/profile/portfolio/items` 等 8 项 | `profile.html` |
| `classroom_app/routers/review.py` | 3 | `GET /feedback-review`, `GET /api/feedback-review/bootstrap`, `POST /api/feedback-review/items` | `feedback_review.html` |
| `classroom_app/routers/session.py` | 3 | `GET /api/session/active`, `POST /api/session/invalidate/{user_id}`, `GET /api/session/my-info` | - |
| `classroom_app/routers/signatures.py` | 14 | `GET /list`, `GET /schools`, `GET /teachers`, `POST /upload` 等 8 项 | - |
| `classroom_app/routers/smart_classroom.py` | 4 | `GET /{class_offering_id}/sessions/{session_id}/smart-checkin`, `POST /{class_offering_id}/sessions/{session_id}/smart-checkin/sync`, `GET /{class_offering_id}/smart-attendance/analytics`, `GET /{class_offering_id}/smart-attendance/export` | - |
| `classroom_app/routers/ui_parts/assignment_pages.py` | 5 | `GET /assignment/{assignment_id}`, `GET /assignment/{assignment_id}/wrong-summary`, `GET /api/assignments/{assignment_id}/wrong-summary/status`, `POST /api/assignments/{assignment_id}/wrong-summary/reorganize` 等 5 项 | `status.html`, `assignment_detail_teacher.html`, `assignment_detail_student.html`, `assignment_wrong_summary.html`, `submission_detail.html` |
| `classroom_app/routers/ui_parts/auth.py` | 14 | `GET /`, `GET /student/login`, `GET /teacher/login`, `GET /teacher/register` 等 8 项 | `student_login_v4.html`, `teacher_login_v4.html`, `status.html`, `permission_denied.html` |
| `classroom_app/routers/ui_parts/classroom.py` | 1 | `GET /classroom/{class_offering_id}` | `classroom_main_v4.html` |
| `classroom_app/routers/ui_parts/dashboard.py` | 1 | `GET /dashboard` | `dashboard.html` |
| `classroom_app/routers/ui_parts/exam_pages.py` | 4 | `GET /manage/exams`, `GET /exam/{exam_id}/edit`, `GET /exam/new`, `GET /exam/take/{assignment_id}` | `manage/exams.html`, `exam_editor.html`, `status.html`, `exam_take.html` |
| `classroom_app/routers/ui_parts/manage_pages.py` | 21 | `GET /manage`, `GET /manage/classes`, `GET /manage/students/{student_id}`, `GET /manage/classrooms` 等 8 项 | `manage/workflow.html`, `manage/classes.html`, `manage/student_detail.html`, `manage/classrooms.html`, `manage/courses.html` 等 19 项 |

## 高风险传统脚本

这些脚本体积较大或被多个模板引用，后续迁移前应先补对应 Playwright/接口冒烟测试。

| 脚本 | 大小 | 模板引用次数 |
| --- | ---: | ---: |
| `js/echarts.min.js` | 1.1 MB | 2 |
| `js/classroom_page.js` | 183.3 KB | 0 |
| `js/materials_manage.js` | 115.4 KB | 0 |
| `js/chat.js` | 115.2 KB | 0 |
| `js/blog.js` | 92.6 KB | 1 |
| `js/teacher_onboarding.js` | 79.3 KB | 2 |
| `js/semester_calendar.js` | 78.4 KB | 0 |
| `js/teacher_whiteboard.js` | 76.3 KB | 2 |
| `js/message_center.js` | 65.9 KB | 0 |
| `js/ai_chat_component.js` | 58.7 KB | 6 |
| `js/ai_workspace_widget.js` | 53.9 KB | 5 |
| `js/dashboard.js` | 48.7 KB | 1 |
| `js/material_viewer.js` | 44.8 KB | 1 |
| `js/marked.min.js` | 39.0 KB | 0 |
| `js/manage_courses.js` | 37.2 KB | 1 |
| `js/learning_progress.js` | 37.1 KB | 0 |
| `js/classroom_interactions.js` | 36.6 KB | 0 |
| `js/classroom_private_messages.js` | 35.7 KB | 0 |

## 迁移验收规则

1. 模板迁移前：在“模板清单”中定位原模板、静态脚本、Vite 入口和 island。
2. 路由迁移前：在“路由与模板线索”中确认相关 router 文件、HTTP 方法和模板响应。
3. 传统脚本迁移前：先记录旧脚本公开的全局函数、DOM 选择器、`data-*` 协议和接口路径。
4. React island 上线后：必须确认旧入口仍可达，旧权限仍由后端判定，旧 API 错误态仍能显示。
5. 每次迁移完成后：重新运行本工具并检查 Vite 入口、模板引用和高风险脚本体积是否符合预期。
