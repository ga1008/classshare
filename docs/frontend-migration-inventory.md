# LanShare 前端迁移清单

> 生成时间：2026-11-22 15:07:48（Asia/Shanghai）。
> 生成命令：`python tools/frontend_migration_inventory.py`。

## 用途

这个清单是前端现代化迁移的功能防漏基线。迁移任何页面前，必须先确认对应模板、脚本、路由和业务入口已经在这里被识别；迁移后再用它对照检查入口是否消失、脚本是否重复、Vite island 是否按页面加载。

## 当前 Vite Islands

| 入口 | 产物 | 共享依赖 |
| --- | --- | --- |
| - | 尚未生成 `static/dist/manifest.json` | - |

## 业务域覆盖图

| 业务域 | 模板数量 | 关键模板 | 关键脚本线索 |
| --- | ---: | --- | --- |
| 作业与提交 | 6 | `assignment_detail_student.html`, `assignment_detail_teacher.html`, `assignment_wrong_summary.html`, `macros/assignment_applicants.html`, `partials/submission_retained_grade.html`, `submission_detail.html` | `js/behavior_tracker.js`, `js/feedback.js`, `js/ui.js`, `js/assessment_editor.js`, `js/assessment_questions.js`, `js/submission_upload.js` 等 25 项 |
| 其他页面 | 99 | `achievements.html`, `assessment_plan_editor.html`, `base.html`, `base_centered.html`, `base_navbar.html`, `career_path.html` | `js/auth.js`, `js/assessment_plan_editor.js`, `js/ls_date_picker.js`, `js/cultivation_identity.js`, `js/cultivation_emblems.js`, `js/message_center_bell.js` 等 43 项 |
| 教师管理中心 | 30 | `manage/academic_overview.html`, `manage/ai.html`, `manage/archive_pipeline.html`, `manage/assessment_plans.html`, `manage/attendance_reports.html`, `manage/classes.html` | `js/manage_ai.js`, `js/manage_assessment_plans.js`, `js/attendance_reports.js`, `js/base_resource_modes.js`, `js/manage_classes.js`, `js/manage_classrooms.js` 等 32 项 |
| 消息、反馈、博客、个人中心 | 23 | `blog.html`, `feedback_review.html`, `manage/profile.html`, `partials/blog_button.html`, `partials/feedback_button.html`, `partials/feedback_modal.html` | `js/blog.js`, `js/feedback_review.js`, `js/echarts.min.js`, `js/profile.js`, `js/cultivation_panel.js`, `js/profile_signatures.js` |
| 系统与超管 | 15 | `manage/system/academic_integrations.html`, `manage/system/agent_keys.html`, `manage/system/ai_usage.html`, `manage/system/blog_crawler.html`, `manage/system/blog_review.html`, `manage/system/diagnostics.html` | `js/manage_academic_integrations.js`, `js/manage_agent_keys.js`, `js/manage_blog_crawler.js`, `js/manage_blog_review.js`, `js/feedback_admin.js`, `js/manage_gongwen_integrations.js` 等 11 项 |
| 考试与试卷 | 9 | `exam_editor.html`, `exam_take.html`, `manage/exams.html`, `partials/classroom_members/exams.html`, `partials/exams/board.html`, `partials/exams/control_panel.html` | `js/auth.js`, `js/ui.js`, `js/lq/domain-modal.js`, `js/assessment_editor.js`, `js/assessment_questions.js`, `js/lq/dropdown.js` 等 27 项 |
| 认证与账号 | 7 | `partials/login_remember.html`, `partials/student_security_modal.html`, `permission_denied.html`, `session_expired.html`, `student_login_v4.html`, `teacher_login_v4.html` | `js/session_expired.js`, `js/student_login.js`, `js/local_draft_scope.js`, `js/teacher_login.js` |
| 资料与文件 | 13 | `manage/academic_final_materials.html`, `manage/material_hub.html`, `manage/materials.html`, `material_reader_unavailable.html`, `material_render_shell.html`, `material_viewer.html` | `js/academic_final_materials.js`, `js/material_hub.js`, `js/base_resource_modes.js`, `js/materials_manage.js`, `js/material_reader_return.js`, `js/teacher_whiteboard.js` 等 9 项 |
| 首页与课堂 | 8 | `classroom_main_v4.html`, `dashboard.html`, `dashboard_teacher.html`, `learning_path.html`, `partials/dashboard_agenda_widget.html`, `partials/dashboard_schedule_tools.html` | `js/tools.js`, `js/lq/navigation.js`, `js/classroom_group_qr.js`, `js/behavior_tracker.js`, `js/feedback.js`, `js/student_security.js` 等 12 项 |

## 模板清单

| 模板 | 继承/引用 | 静态脚本与样式 | Vite 入口 | Island |
| --- | --- | --- | --- | --- |
| `achievements.html` | `base_navbar.html`, `macros/lq/content.html`, `macros/lq/chip.html` | - | - | - |
| `assessment_plan_editor.html` | `partials/lq_editor_head.html`, `partials/lq_page_backdrop.html`, `partials/ai_workspace_mount.html`, `partials/lq_theme_attrs.html` | `css/teacher_editors.css`, `css/signature_point_workflow.css`, `js/auth.js`, `js/assessment_plan_editor.js` | - | - |
| `assignment_detail_student.html` | `base.html`, `partials/message_center_bell.html`, `partials/feedback_button.html`, `partials/profile_button.html` 等 7 项 | `css/assignment-student.css`, `css/assessment-student-material.css`, `css/assessment-appeal.css`, `css/assessment-editor.css`, `js/behavior_tracker.js` 等 19 项 | `frontend/src/islands/assignment-submit-sync.tsx` | `assignment-submit-sync` |
| `assignment_detail_teacher.html` | `base.html`, `partials/feedback_modal.html`, `partials/assessment_kind_control.html`, `partials/markdown_assets.html` 等 5 项 | `css/exam_paper_preview.css`, `css/ordinary_grade_kind_controls.css`, `css/assignment_teacher.css`, `css/assessment-appeal.css`, `js/echarts.min.js` 等 14 项 | - | - |
| `assignment_wrong_summary.html` | `base.html`, `partials/markdown_assets.html`, `macros/app_topbar.html` | `js/grading_feedback.js`, `js/lq/domain-modal.js` | - | - |
| `base.html` | `partials/lq_editor_head.html`, `partials/lq_page_backdrop.html`, `partials/vite_islands.html`, `partials/ai_workspace_mount.html` 等 5 项 | `js/auth.js`, `js/ls_date_picker.js`, `js/cultivation_identity.js`, `js/cultivation_emblems.js`, `js/message_center_bell.js` 等 6 项 | - | - |
| `base_centered.html` | `base.html`, `partials/site_record_footer.html` | `css/lq/pages/centered.css`, `css/lq/pages/login.css`, `css/lq/pages/status.css`, `js/ui.js` | - | - |
| `base_navbar.html` | `base.html`, `partials/lq_shell_prepaint.html`, `partials/lq_navbar_topbar.html`, `partials/lq_report_card_topbar.html` 等 10 项 | `js/navbar_lq.js`, `css/lq/pages/navbar.css`, `js/ui.js`, `js/student_security.js`, `js/feedback.js` 等 6 项 | - | - |
| `blog.html` | `base_navbar.html`, `partials/markdown_assets.html` | `css/blog-paper.css`, `css/blog_moderation.css`, `js/blog.js` | - | - |
| `career_path.html` | `base.html` | `css/career_path.css`, `js/career_tools_client.js`, `js/career_path_network.js`, `js/career_path_app.js` | - | - |
| `classroom_main_v4.html` | `base.html`, `partials/markdown_assets.html`, `partials/student_security_modal.html`, `partials/feedback_modal.html` 等 23 项 | `css/ordinary_grade_kind_controls.css`, `css/classroom_group_qr.css`, `css/classroom_closeout.css`, `css/classroom_members.css`, `css/attendance_reports.css` 等 19 项 | `frontend/src/islands/classroom-page.tsx` | `classroom-page`, `assignment-authoring-sync`, `exam-assign-sync` |
| `dashboard.html` | `base_navbar.html`, `partials/dashboard_schedule_tools.html`, `partials/student_dashboard_schedule.html`, `partials/semester_calendar_panel.html` 等 9 项 | `js/learning_certificate_reveal.js` | `frontend/src/islands/dashboard-quick-actions.tsx` | `dashboard-workspace` |
| `dashboard_teacher.html` | `manage/layout.html`, `partials/dashboard_schedule_tools.html`, `partials/semester_calendar_panel.html`, `macros/ui_explanation.html` 等 8 项 | `js/global_search.js` | `frontend/src/islands/dashboard-quick-actions.tsx` | `dashboard-workspace` |
| `dev/lq.html` | `base.html`, `dev/lq_presentation.html`, `dev/lq_interactions.html`, `dev/lq_content.html` 等 9 项 | `css/lq/preview.css`, `js/lq/preview.js` | - | - |
| `dev/lq_business.html` | `macros/lq/business.html`, `macros/lq/upload.html`, `macros/lq/forms.html` | - | - | - |
| `dev/lq_centered.html` | `base_centered.html`, `macros/lq/button.html`, `macros/lq/forms.html` | `css/lq/preview.css`, `js/lq/preview-shell.js` | - | - |
| `dev/lq_content.html` | `macros/lq/content.html`, `macros/lq/composer.html` | `vendor/twemoji/svg/2b50.svg`, `vendor/twemoji/svg/2764.svg`, `vendor/twemoji/svg/2615.svg` | - | - |
| `dev/lq_data.html` | `macros/lq/tables.html`, `macros/lq/selection.html`, `macros/lq/button.html` | - | - | - |
| `dev/lq_insights.html` | `macros/lq/insights.html` | - | - | - |
| `dev/lq_interactions.html` | `macros/lq/button.html`, `macros/lq/forms.html`, `macros/lq/navigation.html`, `macros/lq/collapsible.html` 等 6 项 | - | - | - |
| `dev/lq_presentation.html` | `macros/lq/button.html`, `macros/lq/chip.html`, `macros/lq/chip-row.html`, `macros/lq/indicators.html` 等 5 项 | - | - | - |
| `dev/lq_shell.html` | `partials/lq_editor_head.html`, `partials/ai_workspace_mount.html`, `partials/lq_theme_attrs.html`, `macros/lq/shells.html` 等 9 项 | `css/lq/preview.css`, `js/lq/preview-shell.js` | - | - |
| `dev/lq_status.html` | `macros/lq/status.html`, `macros/lq/forms.html` | - | - | - |
| `dev/lq_workspace.html` | `macros/lq/workspace.html`, `macros/lq/forms.html`, `macros/lq/button.html` | - | - | - |
| `error.html` | `base_centered.html`, `macros/lq/button.html`, `macros/lq/icons.generated.html` | - | - | - |
| `exam_editor.html` | `partials/lq_editor_head.html`, `partials/lq_page_backdrop.html`, `partials/markdown_assets.html`, `partials/ai_workspace_mount.html` 等 5 项 | `css/exam_paper_preview.css`, `css/exam_editor.css`, `css/assessment-editor.css`, `js/auth.js`, `js/ui.js` 等 12 项 | - | - |
| `exam_take.html` | `partials/lq_editor_head.html`, `partials/lq_page_backdrop.html`, `partials/submission_retained_grade.html`, `partials/markdown_assets.html` 等 7 项 | `css/exam-take.css`, `css/assessment-student-material.css`, `css/assessment-appeal.css`, `css/assessment-editor.css`, `js/auth.js` 等 23 项 | - | - |
| `feedback_review.html` | `base_navbar.html`, `macros/lq/content.html` | `js/feedback_review.js` | - | - |
| `learning_path.html` | `base_navbar.html`, `partials/cultivation_card.html`, `macros/lq/content.html` | `js/learning_path.js` | - | - |
| `lesson_plan_editor.html` | `partials/lq_editor_head.html`, `partials/lq_page_backdrop.html`, `partials/ai_workspace_mount.html`, `partials/lq_theme_attrs.html` | `css/teacher_editors.css`, `js/auth.js`, `js/lesson_plan_editor.js` | - | - |
| `lessondoc_editor.html` | `base.html` | `css/lessondoc_editor.css`, `css/ui_color_picker.css`, `js/lessondoc_editor/index.js` | - | - |
| `macros/app_topbar.html` | - | - | - | - |
| `macros/app_utility_menus.html` | `macros/lq/nav-menu.html` | - | - | - |
| `macros/assignment_applicants.html` | `macros/lq/chip.html` | - | - | - |
| `macros/classroom_menus.html` | `macros/lq/nav-menu.html` | - | - | - |
| `macros/lq/business.html` | `macros/lq/button.html`, `macros/lq/indicators.html`, `macros/lq/status.html` | - | - | - |
| `macros/lq/button.html` | `macros/lq/icons.generated.html`, `macros/lq/indicators.html` | - | - | - |
| `macros/lq/chip-row.html` | `macros/lq/chip.html`, `macros/lq/button.html` | - | - | - |
| `macros/lq/chip.html` | `macros/lq/icons.generated.html` | - | - | - |
| `macros/lq/collapsible.html` | - | - | - | - |
| `macros/lq/composer.html` | `macros/lq/button.html` | - | - | - |
| `macros/lq/content.html` | `macros/lq/button.html` | - | - | - |
| `macros/lq/dialogs.html` | `macros/lq/button.html` | - | - | - |
| `macros/lq/forms.html` | - | - | - | - |
| `macros/lq/icons.generated.html` | - | - | - | - |
| `macros/lq/indicators.html` | - | - | - | - |
| `macros/lq/insights.html` | `macros/lq/indicators.html` | - | - | - |
| `macros/lq/menus.html` | `macros/lq/icons.generated.html` | - | - | - |
| `macros/lq/nav-menu.html` | `macros/lq/icons.generated.html`, `macros/lq/menus.html` | - | - | - |
| `macros/lq/navigation.html` | `macros/lq/indicators.html` | - | - | - |
| `macros/lq/selection.html` | - | - | - | - |
| `macros/lq/shells.html` | `macros/lq/button.html`, `macros/lq/icons.generated.html` | - | - | - |
| `macros/lq/skeleton.html` | - | - | - | - |
| `macros/lq/status.html` | `macros/lq/button.html`, `macros/lq/indicators.html` | - | - | - |
| `macros/lq/tables.html` | `macros/lq/button.html` | - | - | - |
| `macros/lq/tooltips.html` | - | - | - | - |
| `macros/lq/upload.html` | `macros/lq/button.html` | - | - | - |
| `macros/lq/workspace.html` | - | - | - | - |
| `macros/manage_icons.html` | `macros/lq/icons.generated.html` | - | - | - |
| `macros/manage_insights.html` | - | - | - | - |
| `macros/manage_page.html` | `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/lq/content.html`, `macros/lq/forms.html` | - | - | - |
| `macros/ui_explanation.html` | - | - | - | - |
| `macros/user_ui_preferences.html` | `macros/ui_explanation.html` | - | - | - |
| `manage/academic_final_materials.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/content.html`, `macros/ui_explanation.html` 等 5 项 | `css/material_workflows.css`, `css/academic_final_materials.css`, `css/signature_point_workflow.css`, `js/academic_final_materials.js` | - | - |
| `manage/academic_overview.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` | - | - | - |
| `manage/ai.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` | `js/manage_ai.js` | - | - |
| `manage/archive_pipeline.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/lq/content.html` 等 5 项 | - | - | - |
| `manage/assessment_plans.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/lq/content.html` 等 6 项 | `css/material_workflows.css`, `css/signature_point_workflow.css`, `js/manage_assessment_plans.js` | - | - |
| `manage/attendance_reports.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/forms.html` | `css/attendance_reports.css`, `js/attendance_reports.js` | - | - |
| `manage/classes.html` | `manage/layout.html`, `partials/academic_sync_dialog.html`, `macros/manage_page.html`, `macros/ui_explanation.html` 等 7 项 | `js/base_resource_modes.js`, `js/manage_classes.js` | - | - |
| `manage/classrooms.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` 等 8 项 | `js/manage_classrooms.js` | - | - |
| `manage/course_schedule.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/forms.html` | `js/manage_course_schedule.js` | - | - |
| `manage/course_schedule_editor.html` | `manage/layout.html`, `macros/app_topbar.html` | `js/course_schedule_editor.js` | - | - |
| `manage/courses.html` | `manage/layout.html`, `partials/learning_material_selector.html`, `partials/academic_sync_dialog.html`, `macros/manage_page.html` 等 10 项 | `js/base_resource_modes.js`, `js/manage_courses.js`, `js/lessondoc_wizard.js` | - | - |
| `manage/exams.html` | `manage/layout.html`, `partials/exams/pagehead.html`, `partials/exams/control_panel.html`, `partials/exams/board.html` 等 10 项 | `css/exam_paper_preview.css`, `css/ordinary_grade_kind_controls.css` | - | - |
| `manage/gongwen.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/manage_insights.html`, `macros/app_topbar.html` | `js/manage_gongwen.js` | - | - |
| `manage/layout.html` | `partials/lq_editor_head.html`, `partials/lq_shell_prepaint.html`, `partials/lq_page_backdrop.html`, `manage/lq_sidebar.html` 等 15 项 | `js/manage_lq_pilot.js`, `js/auth.js`, `js/manage_filter_chips.js`, `js/message_center_bell.js`, `js/ui.js` 等 9 项 | - | - |
| `manage/lesson_plans.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/content.html`, `macros/lq/forms.html` 等 5 项 | `js/manage_lesson_plans.js` | - | - |
| `manage/life_tips.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` | `js/manage_life_tips.js` | - | - |
| `manage/lq_sidebar.html` | `macros/manage_icons.html`, `macros/lq/icons.generated.html`, `macros/ui_explanation.html` | - | - | - |
| `manage/lq_topbar.html` | `manage/lq_utility.html`, `macros/lq/shells.html` | - | - | - |
| `manage/lq_utility.html` | `macros/lq/button.html`, `macros/lq/icons.generated.html`, `macros/user_ui_preferences.html`, `partials/social_entry.html` | - | - | - |
| `manage/material_hub.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/chip-row.html`, `macros/app_topbar.html` | `js/material_hub.js` | - | - |
| `manage/materials.html` | `manage/layout.html`, `partials/materials/library_card.html`, `partials/materials/modals_generate.html`, `partials/materials/modals_repository.html` 等 9 项 | `css/materials_library.css`, `js/base_resource_modes.js`, `js/materials_manage.js` | `frontend/src/islands/materials-manage-page.tsx` | `materials-manage-page` |
| `manage/me_credentials.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/content.html` | - | - | - |
| `manage/offering_hub.html` | `manage/layout.html`, `partials/teaching_stage_bar.html`, `macros/manage_page.html`, `macros/ui_explanation.html` 等 7 项 | `js/manage_offering_hub.js` | - | - |
| `manage/offering_merge.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` | `js/manage_offering_merge.js` | - | - |
| `manage/offerings.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` | `js/manage_offerings.js` | - | - |
| `manage/polls.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` | `js/manage_polls.js` | - | - |
| `manage/profile.html` | `manage/layout.html`, `partials/profile/head.html`, `partials/profile/body.html`, `partials/profile/scripts.html` | - | - | - |
| `manage/semesters.html` | `manage/layout.html`, `partials/semester_calendar_panel.html`, `macros/manage_page.html`, `macros/ui_explanation.html` 等 6 项 | `css/manage_semesters.css`, `js/manage_semesters.js` | - | - |
| `manage/signature_workflows.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/content.html`, `macros/lq/forms.html` 等 5 项 | `css/material_workflows.css`, `js/signature_workflows.js` | - | - |
| `manage/signatures.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/lq/forms.html` 等 7 项 | `css/signature_scope_fields.css`, `js/manage_signatures.js` | - | - |
| `manage/student_detail.html` | `manage/layout.html`, `partials/ability_radar.html`, `macros/app_topbar.html` | - | - | - |
| `manage/system/academic_integrations.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/content.html` 等 6 项 | `js/manage_academic_integrations.js` | - | - |
| `manage/system/agent_keys.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/forms.html` 等 5 项 | `js/manage_agent_keys.js` | - | - |
| `manage/system/ai_usage.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html` | - | - | - |
| `manage/system/blog_crawler.html` | `manage/layout.html`, `macros/manage_page.html` | `js/manage_blog_crawler.js` | - | - |
| `manage/system/blog_review.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/navigation.html` | `css/blog_moderation.css`, `js/manage_blog_review.js` | - | - |
| `manage/system/diagnostics.html` | `manage/layout.html`, `macros/manage_page.html` | - | - | - |
| `manage/system/feedback.html` | `manage/layout.html`, `macros/manage_page.html` | `js/feedback_admin.js` | - | - |
| `manage/system/gongwen_integrations.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/content.html` 等 6 项 | `js/manage_gongwen_integrations.js` | - | - |
| `manage/system/monitor.html` | `manage/layout.html` | `js/manage_system_monitor.js` | - | - |
| `manage/system/organizations.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/forms.html` 等 5 项 | `js/manage_organizations.js` | - | - |
| `manage/system/password_resets.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/content.html` 等 7 项 | - | - | - |
| `manage/system/smart_classroom_integrations.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/app_topbar.html`, `macros/lq/content.html` 等 6 项 | `js/manage_smart_classroom_integrations.js` | - | - |
| `manage/system/super_admin.html` | `manage/layout.html`, `macros/manage_page.html` | - | - | - |
| `manage/system/users.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/button.html`, `macros/app_topbar.html` | `js/manage_users.js` | - | - |
| `manage/system/wanderers.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/button.html`, `macros/lq/chip.html` 等 6 项 | `css/lq/pages/wanderers.css`, `css/lq/pages/wanderer-world.css`, `js/manage_wanderers.js` | - | - |
| `manage/teacher_evaluations.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/lq/content.html` 等 6 项 | `js/manage_teacher_evaluations.js` | - | - |
| `manage/teacher_schedule_query.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/lq/button.html`, `macros/lq/forms.html` 等 6 项 | `css/teacher_schedule_query.css`, `js/manage_teacher_schedule_query.js` | - | - |
| `manage/textbooks.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` 等 8 项 | `js/base_resource_modes.js`, `js/manage_textbooks.js` | - | - |
| `manage/work_inbox.html` | `manage/layout.html`, `macros/manage_page.html`, `macros/ui_explanation.html`, `macros/app_topbar.html` 等 8 项 | - | - | - |
| `material_reader_unavailable.html` | `base_centered.html` | `js/material_reader_return.js` | - | - |
| `material_render_shell.html` | `base.html`, `partials/markdown_assets.html` | `js/material_reader_return.js`, `js/teacher_whiteboard.js`, `js/behavior_tracker.js`, `js/material_render_shell.js` | - | - |
| `material_viewer.html` | `base.html`, `partials/markdown_assets.html` | `js/material_reader_return.js`, `js/teacher_whiteboard.js`, `js/behavior_tracker.js`, `js/material_viewer.js` | - | - |
| `partials/ability_radar.html` | - | - | - | - |
| `partials/academic_sync_dialog.html` | - | - | - | - |
| `partials/ai_workspace_assets.html` | `partials/markdown_assets.html` | `js/ai_chat_component.js`, `js/ai_workspace_widget.js` | - | - |
| `partials/ai_workspace_mount.html` | `partials/ai_workspace_widget.html`, `partials/ai_workspace_assets.html` | `css/ai_workspace.css`, `css/agent_workbench.css` | - | - |
| `partials/ai_workspace_widget.html` | - | - | - | - |
| `partials/app_bottomnav.html` | - | - | - | - |
| `partials/app_topbar_utility_actions.html` | `macros/app_topbar.html`, `macros/app_utility_menus.html`, `partials/social_entry.html` | `js/lq/nav-menu.js` | - | - |
| `partials/assessment_classification_batch.html` | - | `js/assessment_classification_batch.js` | - | - |
| `partials/assessment_kind_control.html` | - | - | - | - |
| `partials/blog_button.html` | - | - | `frontend/src/islands/blog-launcher.tsx` | `blog-launcher` |
| `partials/classroom_group_qr.html` | - | - | - | - |
| `partials/classroom_material_library.html` | `macros/lq/button.html` | - | - | - |
| `partials/classroom_members/alerts.html` | - | - | - | - |
| `partials/classroom_members/exams.html` | - | - | - | - |
| `partials/classroom_members/overview.html` | - | - | - | - |
| `partials/classroom_members/settings.html` | - | - | - | - |
| `partials/classroom_members/workspace.html` | - | - | - | - |
| `partials/classroom_task_preview.html` | `macros/ui_explanation.html`, `macros/assignment_applicants.html` | - | - | - |
| `partials/cultivation_card.html` | - | - | - | - |
| `partials/cultivation_panel.html` | - | - | - | - |
| `partials/dashboard_agenda_widget.html` | - | - | - | - |
| `partials/dashboard_schedule_tools.html` | `macros/lq/nav-menu.html` | - | - | - |
| `partials/exams/board.html` | `partials/assessment_kind_control.html` | - | - | - |
| `partials/exams/control_panel.html` | - | - | - | - |
| `partials/exams/modals.html` | - | - | - | - |
| `partials/exams/pagehead.html` | `macros/manage_page.html` | - | - | - |
| `partials/exams/scripts.html` | - | `js/base_resource_modes.js`, `js/assessment_kind_controls.js`, `js/ui_popover.js`, `js/lq/menus.js`, `js/lq/domain-modal.js` 等 9 项 | - | - |
| `partials/feedback_button.html` | - | - | `frontend/src/islands/feedback-launcher.tsx` | `feedback-launcher` |
| `partials/feedback_modal.html` | - | `css/feedback_conversation.css` | - | - |
| `partials/learning_certificate_reveal.html` | - | - | - | - |
| `partials/learning_material_selector.html` | - | - | - | - |
| `partials/login_remember.html` | `macros/lq/forms.html` | - | - | - |
| `partials/lq_app_bottomnav.html` | `macros/lq/shells.html` | - | - | - |
| `partials/lq_editor_head.html` | `partials/lq_theme_bootstrap.html`, `partials/ui_system_assets.html`, `partials/ai_workspace_mount.html` | `css/user_ui_preferences.css`, `js/user_ui_preferences.js` | - | - |
| `partials/lq_navbar_topbar.html` | `macros/lq/button.html`, `macros/lq/icons.generated.html`, `macros/user_ui_preferences.html` | `js/lq/nav-menu.js` | - | - |
| `partials/lq_page_backdrop.html` | - | - | - | - |
| `partials/lq_report_card_topbar.html` | `partials/lq_navbar_topbar.html` | - | - | - |
| `partials/lq_shell_prepaint.html` | - | - | - | - |
| `partials/lq_theme_attrs.html` | - | - | - | - |
| `partials/lq_theme_bootstrap.html` | - | `js/lq/theme.js`, `js/lq/index.js` | - | - |
| `partials/markdown_assets.html` | - | `es2022_polyfills`, `marked`, `mermaid`, `markdown_runtime` | - | - |
| `partials/materials/library_card.html` | - | - | - | - |
| `partials/materials/modals_generate.html` | - | - | - | - |
| `partials/materials/modals_library.html` | - | - | - | - |
| `partials/materials/modals_repository.html` | - | - | - | - |
| `partials/message_center_bell.html` | - | - | - | - |
| `partials/profile/appearance.html` | `macros/user_ui_preferences.html`, `macros/lq/forms.html`, `macros/lq/chip.html` | - | - | - |
| `partials/profile/body.html` | `partials/profile/hero.html`, `partials/profile/navigation.html`, `partials/profile/overview.html`, `partials/profile/portfolio.html` 等 10 项 | - | - | - |
| `partials/profile/email.html` | `macros/lq/button.html` | - | - | - |
| `partials/profile/head.html` | - | `css/signature_scope_fields.css`, `css/lq/pages/profile-signatures.css`, `css/lq/pages/profile.css`, `css/lq/pages/message-center.css` | - | - |
| `partials/profile/hero.html` | `partials/profile/hero_lq.html`, `partials/cultivation_card.html` | - | - | - |
| `partials/profile/hero_lq.html` | `partials/cultivation_card.html`, `macros/lq/button.html`, `macros/lq/indicators.html`, `macros/lq/status.html` | - | - | - |
| `partials/profile/messages.html` | `macros/lq/button.html` | - | - | `message-center-page`, `message-center-workspace-sync` |
| `partials/profile/navigation.html` | - | - | - | - |
| `partials/profile/overview.html` | `partials/cultivation_card.html`, `partials/cultivation_panel.html`, `partials/ability_radar.html` | - | - | - |
| `partials/profile/portfolio.html` | - | - | - | - |
| `partials/profile/scripts.html` | `partials/markdown_assets.html` | `js/echarts.min.js`, `js/profile.js`, `js/cultivation_panel.js`, `js/profile_signatures.js` | `frontend/src/islands/message-center-page.tsx`, `frontend/src/islands/message-center-workspace-sync.tsx` | - |
| `partials/profile/security.html` | `macros/lq/button.html` | - | - | - |
| `partials/profile/settings.html` | `macros/lq/button.html`, `macros/lq/forms.html` | - | - | - |
| `partials/profile/signatures.html` | - | - | - | - |
| `partials/profile_button.html` | - | - | `frontend/src/islands/profile-launcher.tsx` | `profile-launcher` |
| `partials/semester_calendar_panel.html` | - | - | - | - |
| `partials/session_material_ai_modal.html` | - | - | - | - |
| `partials/site_record_footer.html` | - | - | - | - |
| `partials/social_entry.html` | `macros/lq/icons.generated.html`, `macros/app_topbar.html` | `css/lq/pages/social.css`, `es2022_polyfills`, `marked`, `markdown_runtime`, `js/social/social_client.js` | - | - |
| `partials/student_dashboard_schedule.html` | - | - | - | - |
| `partials/student_security_modal.html` | - | - | - | - |
| `partials/submission_retained_grade.html` | - | - | - | - |
| `partials/teacher_onboarding_modal.html` | - | - | - | - |
| `partials/teaching_stage_bar.html` | - | - | - | - |
| `partials/ui_system_assets.html` | - | `tailwind_app`, `js/ui_explanation.js` | - | - |
| `partials/vite_islands.html` | - | `js/login_attendance_attention.js` | `frontend/src/islands/app-shell.tsx`, `frontend/src/islands/message-center-sync.tsx`, `frontend/src/islands/blog-topbar-sync.tsx` 等 4 项 | `app-shell`, `message-center-sync`, `blog-topbar-sync` 等 4 项 |
| `permission_denied.html` | `base_centered.html`, `macros/lq/button.html`, `macros/lq/icons.generated.html` | - | - | - |
| `points_shop.html` | `base_navbar.html`, `macros/lq/content.html` | `js/points_shop.js` | - | - |
| `profile.html` | `base_navbar.html`, `partials/profile/head.html`, `partials/profile/body.html`, `partials/profile/scripts.html` | - | - | - |
| `report_card.html` | `base_navbar.html`, `partials/lq_shell_prepaint.html`, `macros/lq/content.html` | `js/report_card.js`, `js/echarts.min.js` | - | - |
| `resume/applications.html` | `resume/layout.html` | `js/resume_common.js`, `js/resume_applications.js` | - | - |
| `resume/builder.html` | `resume/layout.html` | `js/resume_common.js`, `js/resume_builder.js` | - | - |
| `resume/home.html` | `resume/layout.html` | `js/resume_common.js`, `js/resume_home.js` | - | - |
| `resume/job_targets.html` | `resume/layout.html` | `js/resume_common.js`, `js/resume_job_targets.js` | - | - |
| `resume/layout.html` | `partials/lq_editor_head.html`, `partials/lq_page_backdrop.html`, `partials/ai_workspace_mount.html`, `partials/lq_theme_attrs.html` | `css/resume_console.css`, `js/ls_date_picker.js`, `js/career_tools_client.js`, `js/resume_shell.js` | - | - |
| `resume/list.html` | `resume/layout.html` | `js/resume_common.js`, `js/resume_list.js` | - | - |
| `resume/personal.html` | `resume/layout.html` | `js/resume_common.js`, `js/resume_personal.js` | - | - |
| `resume/section.html` | `resume/layout.html` | `js/resume_common.js`, `js/resume_section.js` | - | - |
| `session_expired.html` | `base_centered.html`, `macros/lq/button.html`, `macros/lq/icons.generated.html` | `js/session_expired.js` | - | - |
| `status.html` | `base_centered.html`, `macros/lq/button.html`, `macros/lq/icons.generated.html` | - | - | - |
| `student_auth_flow_v4.html` | `base_centered.html`, `macros/lq/button.html` | - | - | - |
| `student_login_v4.html` | `base_centered.html`, `partials/login_remember.html`, `macros/lq/button.html` | `js/student_login.js`, `js/local_draft_scope.js` | - | - |
| `submission_detail.html` | `base.html`, `partials/message_center_bell.html`, `partials/feedback_button.html`, `partials/profile_button.html` 等 6 项 | `css/submission_detail.css`, `css/assessment-appeal.css`, `css/assessment-editor.css`, `js/feedback.js`, `js/api.js` 等 14 项 | `frontend/src/islands/submission-jump-nav.tsx` | `submission-jump-nav` |
| `teacher_evaluation_editor.html` | `partials/lq_editor_head.html`, `partials/lq_page_backdrop.html`, `partials/ai_workspace_mount.html`, `partials/lq_theme_attrs.html` | `css/teacher_editors.css`, `js/auth.js`, `js/teacher_evaluation_editor.js` | - | - |
| `teacher_login_v4.html` | `base_centered.html`, `partials/login_remember.html`, `macros/lq/button.html` | `js/teacher_login.js`, `js/local_draft_scope.js` | - | - |
| `teacher_register_v4.html` | `base_centered.html`, `macros/lq/button.html` | - | - | - |
| `wanderers/home.html` | `base_navbar.html`, `macros/lq/content.html`, `macros/lq/chip.html`, `macros/lq/indicators.html` 等 6 项 | `css/lq/pages/wanderer-home.css`, `css/lq/pages/wanderer-world.css`, `js/wanderer_home.js` | - | - |
| `wrong_book.html` | `base_navbar.html`, `macros/lq/content.html` | `js/echarts.min.js` | - | - |

## 路由与模板线索

| 路由文件 | 路由数量 | 路由样例 | 模板线索 |
| --- | ---: | --- | --- |
| `classroom_app/routers/academic_evaluations.py` | 2 | `POST /sync-current`, `GET /classrooms/{class_offering_id}` | - |
| `classroom_app/routers/achievements.py` | 2 | `GET /achievements`, `GET /api/achievements` | `achievements.html` |
| `classroom_app/routers/agent_bridge.py` | 13 | `POST /children/admit`, `POST /children/{child_id}/finish`, `POST /questions`, `GET /questions/{question_id}` 等 8 项 | - |
| `classroom_app/routers/agent_model_gateway.py` | 2 | `POST /chat/completions`, `POST /messages` | - |
| `classroom_app/routers/agent_tasks.py` | 25 | `GET /bootstrap`, `GET /admin/queue`, `POST /admin/queue/{action}`, `POST /admin/tasks/{task_id}/{action}` 等 8 项 | - |
| `classroom_app/routers/ai.py` | 24 | `POST /ai/generate_assignment`, `POST /submissions/{submission_id}/regrade`, `POST /submissions/{submission_id}/force-regrade`, `POST /submissions/{submission_id}/stop-grading` 等 8 项 | - |
| `classroom_app/routers/approval_workflow.py` | 7 | `GET /types`, `GET /assignments/{assignment_id}/applicant-counts`, `GET /{request_id}`, `POST /{request_id}/approve` 等 7 项 | - |
| `classroom_app/routers/archive_pipeline.py` | 1 | `GET /manage/archive` | `manage/archive_pipeline.html` |
| `classroom_app/routers/assessment_plans.py` | 14 | `GET /classroom/{class_offering_id}/prefill`, `GET /{plan_id}`, `GET /{plan_id}/task`, `POST /generate` 等 8 项 | - |
| `classroom_app/routers/attendance_reports.py` | 2 | `GET /manage/archive/attendance-reports`, `GET /manage/archive/attendance-reports/{report_id}` | `manage/attendance_reports.html` |
| `classroom_app/routers/behavior.py` | 3 | `POST /{class_offering_id}/behavior/batch`, `POST /{class_offering_id}/behavior/diagnostics`, `GET /{class_offering_id}/activity-evidence` | - |
| `classroom_app/routers/blog.py` | 38 | `GET /blog`, `GET /api/blog/posts`, `GET /api/blog/summary`, `GET /api/blog/sections/manage` 等 8 项 | `blog.html` |
| `classroom_app/routers/blog_comments.py` | 6 | `GET /api/blog/posts/{post_id}/floors`, `GET /api/blog/posts/{post_id}/floors/{root_id}/replies`, `PUT /api/blog/comments/{comment_id}`, `POST /api/blog/comments/{comment_id}/delete` 等 6 项 | - |
| `classroom_app/routers/blog_moderation.py` | 13 | `GET /api/blog/posts/{post_id}/moderation`, `POST /api/blog/posts/{post_id}/appeal`, `POST /api/blog/moderation/{target_type}/{target_id}`, `GET /api/blog/moderation/reasons` 等 8 项 | `manage/system/blog_review.html` |
| `classroom_app/routers/blog_studio.py` | 5 | `GET /api/blog/studio/overview`, `GET /api/blog/studio/comments`, `GET /api/blog/studio/fans`, `PUT /api/blog/studio/profile` 等 5 项 | - |
| `classroom_app/routers/calendar_feed.py` | 3 | `GET /api/calendar-feed`, `POST /api/calendar-feed/reset`, `GET /calendar/feed/{token}.ics` | - |
| `classroom_app/routers/career_path.py` | 15 | `GET /career-path`, `GET /api/career-path/state`, `POST /api/career-path/initialize`, `GET /api/career-path/questions` 等 8 项 | `career_path.html` |
| `classroom_app/routers/chat_actions.py` | 2 | `POST /api/ai/chat-actions/{token}/execute`, `POST /api/ai/chat-actions/{token}/cancel` | - |
| `classroom_app/routers/classroom_closeout.py` | 2 | `GET /{class_offering_id}/closeout/summary`, `POST /{class_offering_id}/closeout/execute` | - |
| `classroom_app/routers/classroom_group_qr.py` | 1 | `GET /image` | - |
| `classroom_app/routers/classroom_interactions.py` | 10 | `GET /classrooms/{class_offering_id}/snapshot`, `POST /classrooms/{class_offering_id}/messages/{message_id}/useful`, `POST /classrooms/{class_offering_id}/activities`, `POST /activities/{activity_id}/respond` 等 8 项 | - |
| `classroom_app/routers/classroom_retake.py` | 4 | `GET /{class_offering_id}/retake-students`, `POST /{class_offering_id}/retake-students/detect`, `POST /{class_offering_id}/retake-students/confirm`, `POST /{class_offering_id}/retake-students/revoke` | - |
| `classroom_app/routers/collaboration.py` | 34 | `GET /classrooms/{class_offering_id}/snapshot`, `POST /classrooms/{class_offering_id}/schemes`, `POST /schemes/{scheme_id}/fill`, `POST /schemes/{scheme_id}/random-join` 等 8 项 | - |
| `classroom_app/routers/cultivation_display.py` | 3 | `GET /api/cultivation/emblems`, `GET /api/profile/cultivation-visibility`, `PUT /api/profile/cultivation-visibility` | - |
| `classroom_app/routers/document_renderer.py` | 3 | `GET /jobs/{key}/pages/{page_number}`, `GET /jobs/{key}/download`, `GET /jobs/{key}/metadata` | - |
| `classroom_app/routers/emoji.py` | 3 | `GET /api/classrooms/{class_offering_id}/emoji-panel`, `POST /api/classrooms/{class_offering_id}/custom-emojis`, `GET /api/classrooms/{class_offering_id}/custom-emojis/{emoji_id}/file` | - |
| `classroom_app/routers/feedback.py` | 11 | `POST /api/feedback`, `POST /api/feedback/{feedback_id}/upload`, `GET /api/feedback/{feedback_id}/attachment/{file_hash}`, `GET /api/feedback/my` 等 8 项 | - |
| `classroom_app/routers/files.py` | 20 | `POST /api/files/check`, `POST /api/files/upload/init`, `POST /api/files/upload/chunk`, `POST /api/files/upload/complete` 等 8 项 | - |
| `classroom_app/routers/global_search.py` | 1 | `GET /api/global-search` | - |
| `classroom_app/routers/homework_parts/assignments.py` | 12 | `GET /classrooms/{class_offering_id}/assignments`, `GET /assignments/{assignment_id}/details`, `GET /classrooms/{class_offering_id}/assessment-classifications`, `GET /assignments/{assignment_id}/assessment-kind` 等 8 项 | - |
| `classroom_app/routers/homework_parts/drafts.py` | 6 | `GET /assignments/{assignment_id}/draft`, `POST /assignments/{assignment_id}/draft`, `GET /assignments/{assignment_id}/draft-files/{file_id}`, `POST /assignments/{assignment_id}/attachment-hash-check` 等 6 项 | - |
| `classroom_app/routers/homework_parts/exam_papers.py` | 16 | `GET /exam-papers/review-catalog`, `GET /exam-papers/{paper_id}/review`, `GET /exam-papers`, `PUT /exam-papers/{paper_id}/tags` 等 8 项 | - |
| `classroom_app/routers/homework_parts/exports.py` | 3 | `GET /assignments/{assignment_id}/export-attachments/{class_offering_id}`, `GET /assignments/{assignment_id}/export/{class_offering_id}`, `GET /assignments/{assignment_id}/export-review-docx` | - |
| `classroom_app/routers/homework_parts/grading.py` | 7 | `GET /submissions/{submission_id}/review`, `GET /assignments/{assignment_id}/review-submissions`, `GET /submissions/{submission_id}/review-files`, `POST /assignments/{assignment_id}/submissions/zero-unsubmitted` 等 7 项 | - |
| `classroom_app/routers/homework_parts/group_assignments.py` | 4 | `GET /assignments/{assignment_id}/group-config`, `POST /assignments/{assignment_id}/group-config`, `GET /assignments/{assignment_id}/peer-eval`, `POST /assignments/{assignment_id}/peer-eval` | - |
| `classroom_app/routers/homework_parts/submissions.py` | 8 | `GET /assignments/{assignment_id}/submissions`, `DELETE /submissions/{submission_id}`, `POST /submissions/{submission_id}/files`, `DELETE /submission-files/{file_id}` 等 8 项 | - |
| `classroom_app/routers/learning.py` | 26 | `GET /classrooms/{class_offering_id}/members`, `GET /classrooms/mine`, `GET /classrooms/{class_offering_id}/learning/snapshot`, `GET /learning/cultivation-profile` 等 8 项 | - |
| `classroom_app/routers/learning_path.py` | 3 | `GET /learning-path`, `GET /api/learning-path/bootstrap`, `POST /api/learning-path/items` | `learning_path.html` |
| `classroom_app/routers/lesson_plans.py` | 14 | `GET /classroom/{class_offering_id}/generation-plan`, `POST /classroom/{class_offering_id}/session-draft`, `GET /{plan_id}`, `GET /{plan_id}/task` 等 8 项 | - |
| `classroom_app/routers/lessondoc.py` | 14 | `POST /api/lessondoc/packs`, `GET /api/lessondoc/packs`, `GET /api/lessondoc/classrooms/{class_offering_id}/pack`, `GET /api/lessondoc/packs/by-root/{root_material_id}` 等 8 项 | - |
| `classroom_app/routers/lessondoc_editor.py` | 17 | `GET /packs/{pack_id}/document`, `GET /editability/{material_id}`, `GET /legacy-context/{material_id}`, `POST /packs/{pack_id}/validate` 等 8 项 | `lessondoc_editor.html` |
| `classroom_app/routers/login_attendance.py` | 20 | `GET /history`, `GET /dashboard/visibility`, `GET /calendar`, `GET /calendar/sessions/{session_id}` 等 8 项 | - |
| `classroom_app/routers/manage_parts/academic_teachers.py` | 5 | `GET /academic/teachers/options`, `POST /academic/teachers/query`, `POST /academic/teachers/sync`, `POST /academic/teachers/{teacher_no:path}/sync` 等 5 项 | - |
| `classroom_app/routers/manage_parts/base_resource_modes.py` | 13 | `GET /classes/{class_id}/attributes`, `PATCH /classes/{class_id}/attributes`, `GET /classes/{class_id}/students`, `PATCH /students/{student_id}` 等 8 项 | - |
| `classroom_app/routers/manage_parts/classes_courses_classes.py` | 20 | `POST /classes/create`, `POST /classes/custom`, `POST /classes/sync-current-academic`, `POST /academic-sync/preview` 等 8 项 | - |
| `classroom_app/routers/manage_parts/classes_courses_courses.py` | 7 | `POST /courses/save`, `POST /courses/sync-current-academic`, `POST /courses/ai-generate-lessons`, `POST /courses/create` 等 7 项 | - |
| `classroom_app/routers/manage_parts/classes_courses_offerings.py` | 13 | `GET /class_offerings/bootstrap/candidates`, `POST /class_offerings/bootstrap/execute`, `GET /class_offerings/merge/candidates`, `POST /class_offerings/merge/preview` 等 8 项 | - |
| `classroom_app/routers/manage_parts/classes_courses_onboarding.py` | 5 | `GET /teacher-onboarding/state`, `POST /teacher-onboarding/dismiss`, `POST /teacher-onboarding/classes/create`, `POST /teacher-onboarding/course-description` 等 5 项 | - |
| `classroom_app/routers/manage_parts/integrations.py` | 43 | `GET /system/academic-credentials`, `GET /system/academic-sync-capabilities`, `POST /system/academic-sync`, `POST /system/integration-request-probe` 等 8 项 | - |
| `classroom_app/routers/manage_parts/organization_suggestions.py` | 1 | `GET /organization-suggestions` | - |
| `classroom_app/routers/manage_parts/schedule_editor.py` | 19 | `GET /academic/course-schedule/editor`, `POST /academic/course-schedule/editor/drafts`, `DELETE /academic/course-schedule/editor/drafts/{draft_id}`, `POST /academic/course-schedule/editor/push` 等 8 项 | - |
| `classroom_app/routers/manage_parts/semesters_textbooks.py` | 9 | `POST /semesters/save`, `POST /semesters/{semester_id}/calendar/sync`, `GET /semesters/{semester_id}/calendar/status`, `POST /semesters/calendar/sync-current` 等 8 项 | - |
| `classroom_app/routers/manage_parts/system_config.py` | 47 | `GET /system/teachers/{teacher_id}`, `GET /system/background-tasks`, `GET /system/runtime-metrics`, `GET /system/ai-providers` 等 8 项 | - |
| `classroom_app/routers/manage_parts/system_monitor.py` | 5 | `GET /system/monitor/snapshot`, `GET /system/monitor/processes`, `POST /system/monitor/processes/{pid:int}/terminate`, `POST /system/monitor/memory/optimize` 等 5 项 | - |
| `classroom_app/routers/material_hub.py` | 3 | `GET /manage/library`, `GET /api/materials/hub/search`, `POST /api/materials/hub/ai-search` | `manage/material_hub.html` |
| `classroom_app/routers/material_knowledge_search.py` | 3 | `GET /api/material-knowledge/search`, `GET /api/manage/system/material-search/status`, `POST /api/manage/system/material-search/reindex` | - |
| `classroom_app/routers/material_whiteboards.py` | 4 | `GET /{board_key}`, `PUT /{board_key}`, `PATCH /{board_key}`, `DELETE /{board_key}` | - |
| `classroom_app/routers/materials_parts/academic_final_materials.py` | 8 | `GET /manage/archive/academic-grade-registers`, `GET /manage/archive/academic-exam-analyses`, `GET /api/academic-final-materials/candidates`, `GET /api/academic-final-materials` 等 8 项 | `manage/academic_final_materials.html` |
| `classroom_app/routers/materials_parts/ai_import.py` | 10 | `GET /api/materials/ai-generation/candidates`, `GET /api/materials/ai-generation/assignments`, `POST /api/materials/ai-generate`, `POST /api/materials/ai-import` 等 8 项 | - |
| `classroom_app/routers/materials_parts/exports.py` | 14 | `GET /api/materials/ai-import-records/{record_id}/export`, `GET /api/materials/ai-import-records/{record_id}/render-preview`, `GET /api/materials/{material_id}/ai-import/export`, `GET /materials/view/{material_id}` 等 8 项 | `material_reader_unavailable.html`, `material_viewer.html`, `material_render_shell.html` |
| `classroom_app/routers/materials_parts/final_materials.py` | 15 | `GET /api/classrooms/{class_offering_id}/grade-publication`, `GET /api/classrooms/{class_offering_id}/grade-publication/preview`, `POST /api/classrooms/{class_offering_id}/grade-publication/publish`, `POST /api/classrooms/{class_offering_id}/grade-publication/withdraw` 等 8 项 | - |
| `classroom_app/routers/materials_parts/learning.py` | 13 | `GET /api/classrooms/{class_offering_id}/learning-materials`, `POST /api/classrooms/{class_offering_id}/learning-materials`, `DELETE /api/classrooms/{class_offering_id}/learning-materials`, `GET /api/materials/{material_id}/learning-bindings` 等 8 项 | - |
| `classroom_app/routers/materials_parts/library.py` | 24 | `GET /manage/library/materials`, `GET /manage/archive/postclass-materials`, `GET /manage/archive/grading-rubrics`, `GET /manage/archive/ordinary-grade-records` 等 8 项 | `manage/materials.html` |
| `classroom_app/routers/materials_parts/node_ops.py` | 6 | `POST /api/materials/folders`, `POST /api/materials/files`, `GET /api/materials/folder-options`, `POST /api/materials/{material_id}/move` 等 6 项 | - |
| `classroom_app/routers/message_center.py` | 19 | `GET /message-center`, `GET /api/message-center/bootstrap`, `GET /api/message-center/summary`, `GET /api/message-center/items` 等 8 项 | - |
| `classroom_app/routers/mp/auth.py` | 5 | `POST /login`, `POST /bind/student`, `POST /bind/teacher`, `GET /me` 等 5 项 | - |
| `classroom_app/routers/mp/classroom.py` | 1 | `GET /live` | - |
| `classroom_app/routers/mp/life_tips.py` | 1 | `POST /feedback` | - |
| `classroom_app/routers/mp/subscribe.py` | 2 | `GET /config`, `POST /report` | - |
| `classroom_app/routers/mp/tasks.py` | 1 | `GET /assignment/{assignment_id}` | - |
| `classroom_app/routers/mp/teacher.py` | 6 | `GET /tasks`, `GET /assignment/{assignment_id}/grading`, `POST /assignment/{assignment_id}/nudge`, `GET /assignment/{assignment_id}/nudge-status` 等 6 项 | - |
| `classroom_app/routers/mp/todos.py` | 1 | `POST /{todo_id}/update` | - |
| `classroom_app/routers/points_shop.py` | 3 | `GET /points`, `GET /api/points`, `POST /api/points/redeem` | `points_shop.html` |
| `classroom_app/routers/polls.py` | 12 | `GET /classrooms/{class_offering_id}/snapshot`, `GET /classrooms/{class_offering_id}/candidates`, `POST /classrooms/{class_offering_id}/polls`, `GET /manage/list` 等 8 项 | - |
| `classroom_app/routers/profile.py` | 18 | `GET /profile`, `GET /api/profile/bootstrap`, `GET /api/profile/portfolio`, `POST /api/profile/portfolio/items` 等 8 项 | `manage/profile.html`, `profile.html` |
| `classroom_app/routers/project_topics.py` | 17 | `GET /packages`, `POST /packages/import`, `GET /packages/{package_id}`, `POST /packages/{package_id}/map` 等 8 项 | - |
| `classroom_app/routers/prompt_pool.py` | 1 | `POST /record` | - |
| `classroom_app/routers/report_card.py` | 2 | `GET /report-card`, `GET /api/report-card` | `report_card.html` |
| `classroom_app/routers/resume_console.py` | 56 | `GET /resume`, `GET /resume/profile/personal`, `GET /resume/job-targets`, `GET /resume/applications` 等 8 项 | `resume/home.html`, `resume/personal.html`, `resume/job_targets.html`, `resume/applications.html`, `resume/section.html` 等 7 项 |
| `classroom_app/routers/review.py` | 3 | `GET /feedback-review`, `GET /api/feedback-review/bootstrap`, `POST /api/feedback-review/items` | `feedback_review.html` |
| `classroom_app/routers/session.py` | 6 | `GET /api/session/active`, `POST /api/session/invalidate/{user_id}`, `GET /api/session/my-info`, `GET /api/session/remembered` 等 6 项 | - |
| `classroom_app/routers/session_knowledge_trees.py` | 13 | `GET /{class_offering_id}/sessions/{session_id}/knowledge-tree`, `POST /{class_offering_id}/sessions/{session_id}/knowledge-tree/generate`, `GET /{class_offering_id}/knowledge-trees`, `POST /{class_offering_id}/knowledge-trees/generate` 等 8 项 | - |
| `classroom_app/routers/signatures.py` | 41 | `GET /notification-readiness`, `GET /list`, `GET /schools`, `GET /teachers` 等 8 项 | - |
| `classroom_app/routers/smart_attendance_appeals.py` | 2 | `GET /{offering_id}/smart-attendance/records/{source_key}`, `POST /{offering_id}/smart-attendance/records/{source_key}/appeals` | - |
| `classroom_app/routers/smart_classroom.py` | 4 | `GET /{class_offering_id}/sessions/{session_id}/smart-checkin`, `POST /{class_offering_id}/sessions/{session_id}/smart-checkin/sync`, `GET /{class_offering_id}/smart-attendance/analytics`, `GET /{class_offering_id}/smart-attendance/export` | - |
| `classroom_app/routers/smart_classroom_write.py` | 23 | `POST /students/{student_id}/corrections`, `GET /students/{student_id}/push`, `POST /students/{student_id}/push/check`, `POST /students/{student_id}/push/{intent_id}/retry` 等 8 项 | - |
| `classroom_app/routers/social.py` | 50 | `GET /friends`, `GET /friends/requests`, `POST /friends/requests`, `POST /friends/requests/{request_id}/accept` 等 8 项 | - |
| `classroom_app/routers/social_ws.py` | 1 | `WEBSOCKET /ws/social` | - |
| `classroom_app/routers/teacher_evaluations.py` | 14 | `GET /classroom/{class_offering_id}/prefill`, `GET /{evaluation_id}`, `GET /{evaluation_id}/task`, `POST /generate` 等 8 项 | - |
| `classroom_app/routers/ui_parts/assessment_plan_pages.py` | 3 | `GET /manage/archive/assessment-plans`, `GET /assessment-plan/{plan_id}/edit`, `GET /assessment-plan/{plan_id}/preview` | `manage/assessment_plans.html`, `assessment_plan_editor.html` |
| `classroom_app/routers/ui_parts/assignment_pages.py` | 5 | `GET /assignment/{assignment_id}`, `GET /assignment/{assignment_id}/wrong-summary`, `GET /api/assignments/{assignment_id}/wrong-summary/status`, `POST /api/assignments/{assignment_id}/wrong-summary/reorganize` 等 5 项 | `status.html`, `assignment_detail_teacher.html`, `assignment_detail_student.html`, `assignment_wrong_summary.html`, `submission_detail.html` |
| `classroom_app/routers/ui_parts/auth.py` | 20 | `GET /student/login/identity`, `POST /student/login/identity`, `POST /student/password/setup`, `GET /student/password/forgot` 等 8 项 | `teacher_login_v4.html`, `student_login_v4.html`, `student_auth_flow_v4.html`, `status.html`, `permission_denied.html` |
| `classroom_app/routers/ui_parts/classroom.py` | 2 | `GET /api/classrooms/{class_offering_id}/member-panels/{panel_key}`, `GET /classroom/{class_offering_id}` | `partials/classroom_members/{panel_key}.html`, `classroom_main_v4.html` |
| `classroom_app/routers/ui_parts/dashboard.py` | 4 | `GET /dashboard`, `GET /api/dashboard/workspace`, `GET /api/dashboard/calendar`, `GET /api/dashboard/course-schedule/overview` | `dashboard_teacher.html`, `dashboard.html` |
| `classroom_app/routers/ui_parts/design_system.py` | 1 | `GET /dev/lq` | `dev/lq_centered.html`, `dev/lq_shell.html`, `dev/lq.html` |
| `classroom_app/routers/ui_parts/exam_pages.py` | 4 | `GET /manage/library/exams`, `GET /exam/{exam_id}/edit`, `GET /exam/new`, `GET /exam/take/{assignment_id}` | `manage/exams.html`, `exam_editor.html`, `status.html`, `exam_take.html` |
| `classroom_app/routers/ui_parts/lesson_plan_pages.py` | 3 | `GET /manage/library/lesson-plans`, `GET /lesson-plan/{plan_id}/edit`, `GET /lesson-plan/{plan_id}/preview` | `manage/lesson_plans.html`, `lesson_plan_editor.html` |
| `classroom_app/routers/ui_parts/life_tip_pages.py` | 4 | `GET /manage/system/life-tips`, `GET /api/life-tips/manage/list`, `POST /api/life-tips/manage/create`, `POST /api/life-tips/manage/{tip_id}/status` | `manage/life_tips.html` |
| `classroom_app/routers/ui_parts/manage_pages_academic.py` | 9 | `GET /manage/academic`, `GET /manage/academic/teachers/schedule`, `GET /manage/academic/classrooms`, `GET /manage/academic/integrations` 等 8 项 | `manage/academic_overview.html`, `manage/teacher_schedule_query.html`, `manage/classrooms.html`, `manage/system/academic_integrations.html`, `manage/system/smart_classroom_integrations.html` 等 9 项 |
| `classroom_app/routers/ui_parts/manage_pages_admin.py` | 11 | `GET /manage/system`, `GET /manage/system/users`, `GET /manage/system/super-admin`, `GET /manage/system/organizations` 等 8 项 | `manage/system/users.html`, `manage/system/organizations.html`, `manage/system/feedback.html`, `manage/system/diagnostics.html`, `manage/system/monitor.html` 等 9 项 |
| `classroom_app/routers/ui_parts/manage_pages_library.py` | 2 | `GET /manage/library/courses`, `GET /manage/library/textbooks` | `manage/courses.html`, `manage/textbooks.html` |
| `classroom_app/routers/ui_parts/manage_pages_me.py` | 4 | `GET /manage/me/credentials`, `GET /manage/me/signature-workflows`, `GET /manage/me/signatures`, `GET /manage/me/password-resets` | `manage/me_credentials.html`, `manage/signature_workflows.html`, `manage/signatures.html`, `manage/system/password_resets.html` |
| `classroom_app/routers/ui_parts/manage_pages_teaching.py` | 10 | `GET /manage/teaching/workflow`, `GET /manage/teaching/classes`, `GET /manage/students/{student_id}`, `GET /manage/teaching/semesters` 等 8 项 | `manage/classes.html`, `manage/student_detail.html`, `manage/semesters.html`, `manage/offerings.html`, `manage/offering_hub.html` 等 7 项 |
| `classroom_app/routers/ui_parts/poll_pages.py` | 1 | `GET /manage/library/polls` | `manage/polls.html` |
| `classroom_app/routers/ui_parts/teacher_evaluation_pages.py` | 4 | `GET /manage/archive/teacher-evaluations`, `GET /manage/teacher-evaluations`, `GET /teacher-evaluation/{evaluation_id}/edit`, `GET /teacher-evaluation/{evaluation_id}/preview` | `manage/teacher_evaluations.html`, `teacher_evaluation_editor.html` |
| `classroom_app/routers/user_ui_preferences.py` | 2 | `GET /api/profile/ui-preferences`, `PATCH /api/profile/ui-preferences` | - |
| `classroom_app/routers/wanderer_internal.py` | 2 | `GET /api/internal/wanderers/rooms`, `POST /api/internal/wanderers/classroom-broadcast` | - |
| `classroom_app/routers/wanderers.py` | 3 | `GET /api/wanderers/{student_id:int}/memory`, `GET /api/wanderers/{student_id:int}/card`, `GET /wanderers/{slug}` | `wanderers/home.html` |
| `classroom_app/routers/work_inbox.py` | 2 | `GET /manage/me/inbox`, `GET /api/work-inbox` | `manage/work_inbox.html` |
| `classroom_app/routers/wrong_book.py` | 2 | `GET /wrong-book`, `GET /api/wrong-book` | `wrong_book.html` |

## 高风险传统脚本

这些脚本体积较大或被多个模板引用，后续迁移前应先补对应 Playwright/接口冒烟测试。

| 脚本 | 大小 | 模板引用次数 |
| --- | ---: | ---: |
| `js/echarts.min.js` | 1.1 MB | 4 |
| `js/materials_manage.js` | 317.4 KB | 1 |
| `js/blog.js` | 213.1 KB | 1 |
| `js/classroom_page.js` | 160.2 KB | 0 |
| `js/course_schedule_editor.js` | 133.7 KB | 1 |
| `js/chat.js` | 122.7 KB | 0 |
| `js/message_center.js` | 105.9 KB | 0 |
| `js/collaboration.js` | 105.8 KB | 0 |
| `js/semester_calendar.js` | 100.5 KB | 0 |
| `js/classroom_materials.js` | 85.2 KB | 0 |
| `js/teacher_onboarding.js` | 83.0 KB | 2 |
| `js/dashboard.js` | 83.0 KB | 0 |
| `js/ai_chat_component.js` | 79.9 KB | 1 |
| `js/course_schedule_deck.js` | 77.0 KB | 0 |
| `js/attendance_reports.js` | 71.0 KB | 1 |
| `js/approval_workflow.js` | 63.7 KB | 2 |
| `js/career_path_app.js` | 63.2 KB | 1 |
| `js/dashboard_agenda_widget.js` | 59.6 KB | 0 |

## 迁移验收规则

1. 模板迁移前：在“模板清单”中定位原模板、静态脚本、Vite 入口和 island。
2. 路由迁移前：在“路由与模板线索”中确认相关 router 文件、HTTP 方法和模板响应。
3. 传统脚本迁移前：先记录旧脚本公开的全局函数、DOM 选择器、`data-*` 协议和接口路径。
4. React island 上线后：必须确认旧入口仍可达，旧权限仍由后端判定，旧 API 错误态仍能显示。
5. 每次迁移完成后：重新运行本工具并检查 Vite 入口、模板引用和高风险脚本体积是否符合预期。
