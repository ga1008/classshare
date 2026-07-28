import json
import re
import sqlite3
import unittest
from pathlib import Path

from fastapi import HTTPException

from classroom_app.routers.materials_parts.ai_import import (
    _attach_ai_generation_document_source,
)
from classroom_app.routers.materials_parts.final_material_helpers import (
    _academic_year_from_values,
    _build_manage_final_material_context,
    _semester_label_from_value,
)
from classroom_app.routers.materials_parts.ai_import_helpers import (
    _build_ai_import_detail_summary,
    _build_ai_import_record_detail_payload,
    _material_ai_import_export_format,
    _material_ai_import_pdf_export_url,
)
from classroom_app.routers.materials_parts.library import (
    GRADE_RECORD_GENERATE_BLOCKERS,
    GRADE_RECORD_IMPORT_PRESETS,
)
from classroom_app.services.material_ai_import_service import (
    get_material_ai_import_registry,
    resolve_material_ai_import_type,
    validate_material_ai_import_filename,
)
from classroom_app.services.process_material_import_policy import (
    PROCESS_DOCUMENT_IMPORT_ACCEPT,
    validate_process_document_import_file_bytes,
    validate_process_document_import_file_count,
    validate_process_document_import_filename,
)
from classroom_app.services.process_material_import_summary_service import (
    build_process_import_summary,
)
from classroom_app.db import schema_lesson_plans


class ProcessMaterialWorkflowContractTests(unittest.TestCase):
    def test_final_material_academic_period_normalizes_local_and_jwxt_values(self):
        self.assertEqual(_academic_year_from_values("2025-2026学年"), "2025-2026")
        self.assertEqual(_academic_year_from_values("2025"), "2025-2026")
        self.assertEqual(_semester_label_from_value("2025-2026-1"), "第一学期")
        self.assertEqual(_semester_label_from_value("12", academic_term_code=True), "第二学期")
        self.assertEqual(_semester_label_from_value("P03-2026"), "")

    def _final_import_types(self):
        registry = get_material_ai_import_registry()
        final = next(group for group in registry if group["key"] == "final_material")
        return {item["key"] for item in final["types"]}

    def test_grade_record_import_types_are_available_in_registry(self):
        self.assertTrue(
            {
                "assessment_plan",
                "grading_rubric",
                "ordinary_grade_record",
                "exam_grade_record",
            }.issubset(self._final_import_types())
        )

    def test_ai_import_registry_exposes_type_aware_file_format_contract(self):
        registry = get_material_ai_import_registry()
        final = next(group for group in registry if group["key"] == "final_material")
        by_type = {item["key"]: item for item in final["types"]}

        for key in ("ordinary_grade_record", "exam_grade_record"):
            self.assertEqual(by_type[key]["accepted_extensions"], [".xls", ".xlsx"])
            self.assertEqual(by_type[key]["accept"], ".xls,.xlsx")
            self.assertIn("Excel", by_type[key]["accepted_format_label"])
            self.assertIn("学校模板 Excel", by_type[key]["format_hint"])

        self.assertIn(".docx", by_type["assessment_plan"]["accepted_extensions"])
        self.assertIn(".pdf", by_type["assessment_plan"]["recommended_extensions"])

    def test_grade_record_import_rejects_non_excel_before_queueing(self):
        router = Path("classroom_app/routers/materials_parts/ai_import.py").read_text(encoding="utf-8")
        type_meta = resolve_material_ai_import_type("final_material", "ordinary_grade_record")

        validate_material_ai_import_filename(type_meta, "平时成绩记录表.xlsx")
        with self.assertRaises(HTTPException) as cm:
            validate_material_ai_import_filename(type_meta, "平时成绩记录表.pdf")
        self.assertEqual(cm.exception.status_code, 415)
        self.assertIn("仅支持Excel", str(cm.exception.detail))
        self.assertLess(
            router.index("validate_material_ai_import_filename(type_meta, original_name)"),
            router.index("payload_bytes = await file.read()"),
        )

    def test_independent_process_document_import_policy_is_shared_by_frontend_and_backend(self):
        policy = Path("static/js/process_material_import_policy.js").read_text(encoding="utf-8")
        file_picker = Path("static/js/process_material_file_picker.js").read_text(encoding="utf-8")
        assessment = Path("static/js/manage_assessment_plans.js").read_text(encoding="utf-8")
        teacher = Path("static/js/manage_teacher_evaluations.js").read_text(encoding="utf-8")
        lesson = Path("static/js/manage_lesson_plans.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        assessment_template = Path("templates/manage/assessment_plans.html").read_text(encoding="utf-8")
        teacher_template = Path("templates/manage/teacher_evaluations.html").read_text(encoding="utf-8")
        assessment_router = Path("classroom_app/routers/assessment_plans.py").read_text(encoding="utf-8")
        teacher_router = Path("classroom_app/routers/teacher_evaluations.py").read_text(encoding="utf-8")
        lesson_router = Path("classroom_app/routers/lesson_plans.py").read_text(encoding="utf-8")
        action_state = Path("static/js/process_material_action_state.js").read_text(encoding="utf-8")
        modal_helper = Path("static/js/process_material_modal.js").read_text(encoding="utf-8")
        tree_modal = Path("static/js/tree_select_form_modal.js").read_text(encoding="utf-8")

        self.assertEqual(PROCESS_DOCUMENT_IMPORT_ACCEPT, ".doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.gif,.md,.txt")
        validate_process_document_import_file_count([object()])
        with self.assertRaises(HTTPException) as cm:
            validate_process_document_import_file_count([])
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("至少选择一个文件", str(cm.exception.detail))
        with self.assertRaises(HTTPException) as cm:
            validate_process_document_import_file_count([object()] * 9)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("最多一次导入 8 个文件", str(cm.exception.detail))
        validate_process_document_import_filename("考核计划.docx", document_label="考核计划表")
        validate_process_document_import_filename("教案.pdf", document_label="教案")
        with self.assertRaises(HTTPException) as cm:
            validate_process_document_import_filename("成绩记录.xlsx", document_label="考核计划表")
        self.assertEqual(cm.exception.status_code, 415)
        self.assertIn("考核计划表导入暂不支持 .xlsx 文件", str(cm.exception.detail))
        with self.assertRaises(HTTPException) as cm:
            validate_process_document_import_file_bytes(b"", filename="empty.docx")
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("empty.docx", str(cm.exception.detail))
        self.assertIn("空文件", str(cm.exception.detail))
        with self.assertRaises(HTTPException) as cm:
            validate_process_document_import_file_bytes(b"x" * (30 * 1024 * 1024 + 1), filename="large.pdf")
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("超过 30MB", str(cm.exception.detail))

        self.assertIn("PROCESS_DOCUMENT_IMPORT_ACCEPT", policy)
        self.assertIn("PROCESS_DOCUMENT_IMPORT_FORMAT_HINT", policy)
        self.assertIn("getProcessDocumentImportFileKey", policy)
        self.assertIn("getProcessDocumentImportDuplicateProblem", policy)
        self.assertIn("getProcessDocumentImportFileProblem", policy)
        self.assertIn("formatProcessImportFileSize", policy)

        self.assertIn("export function setupProcessMaterialImportPicker", file_picker)
        self.assertIn("export function setProcessMaterialImportBusyState", file_picker)
        self.assertIn("importRoot?.classList.toggle('is-submitting', busyState);", file_picker)
        self.assertIn("importRoot?.setAttribute('aria-busy'", file_picker)
        self.assertIn("overlay?.querySelectorAll('[data-pm-close], [data-lp-close], [data-ap-close], [data-te-close]').forEach", file_picker)
        self.assertIn("control.disabled = busyState;", file_picker)
        self.assertIn("getProcessDocumentImportFileProblem(file, picked.length)", file_picker)
        self.assertIn("getProcessDocumentImportDuplicateProblem(file, picked)", file_picker)
        self.assertLess(
            file_picker.index("getProcessDocumentImportDuplicateProblem(file, picked)"),
            file_picker.index("picked.push(file);"),
        )
        self.assertIn("formatProcessImportFileSize(file.size)", file_picker)
        self.assertIn("const name = escapeHtml(file.name);", file_picker)
        self.assertIn('title="${name}"', file_picker)
        self.assertIn('aria-label="移除 ${name}"', file_picker)
        self.assertIn("const initialMessage = selectionEl?.querySelector('[data-selection-message]');", file_picker)
        self.assertIn("const messageId = initialMessage?.id", file_picker)
        self.assertIn("const renderSelectionMessage = (message) =>", file_picker)
        self.assertIn("const isBusy = () => submit?.dataset.actionBusy === 'true';", file_picker)
        self.assertIn("const updateSubmitState = () =>", file_picker)
        self.assertIn("const ready = picked.length > 0;", file_picker)
        self.assertIn("const busy = isBusy();", file_picker)
        self.assertIn("selectionEl.innerHTML =", file_picker)
        self.assertIn("renderSelectionMessage(summary)", file_picker)
        self.assertIn("selectionEl.innerHTML = renderSelectionMessage(emptyText);", file_picker)
        self.assertIn("data-clear-files", file_picker)
        self.assertIn("disabledAttr", file_picker)
        self.assertIn('aria-label="清空已选择的导入文件"', file_picker)
        self.assertIn("submit.disabled = busy || !ready;", file_picker)
        self.assertIn("submit.classList.toggle('lp-btn--disabled', !ready);", file_picker)
        self.assertIn("submit.setAttribute('aria-disabled', 'true');", file_picker)
        self.assertIn("submit.title = '请先选择要导入解析的文件';", file_picker)
        self.assertIn("const totalSize = picked.reduce((sum, file) => sum + Number(file.size || 0), 0);", file_picker)
        self.assertIn("formatProcessImportFileSize(totalSize)", file_picker)
        self.assertIn("selectionEl.classList.toggle('is-ready', ready);", file_picker)
        self.assertIn("pickButton.disabled = busy;", file_picker)
        self.assertIn("if (input) input.disabled = busy;", file_picker)
        self.assertIn("dropzone?.classList.toggle('is-disabled', busy);", file_picker)
        self.assertIn("listEl?.querySelectorAll('[data-rm]').forEach", file_picker)
        self.assertIn("if (isBusy()) return;", file_picker)
        self.assertIn("event.preventDefault();\n            if (isBusy()) {", file_picker)
        self.assertIn("dropzone.classList.remove('is-over');\n                return;", file_picker)
        self.assertIn("updateSubmitState();", file_picker)
        self.assertLess(file_picker.index("const updateSubmitState = () =>"), file_picker.index("const renderFiles = () =>"))
        self.assertIn("const clearFiles = () =>", file_picker)
        self.assertIn("picked.splice(0, picked.length);", file_picker)
        self.assertIn("clearFiles,", file_picker)
        self.assertIn("getFiles: () => picked.slice()", file_picker)
        self.assertIn("hasFiles: () => picked.length > 0", file_picker)
        self.assertIn("不支持 Excel、压缩包或无扩展名文件", policy)

        for script, policy_attr, label in (
            (assessment, "data-ap-import-policy", "考核项分值合计"),
            (teacher, "data-te-import-policy", "10 项评分与评语"),
            (lesson, "data-lp-import-policy", "课次安排、讲授/PBL 表格"),
        ):
            self.assertIn("from './process_material_action_state.js'", script)
            self.assertIn("from './process_material_modal.js'", script)
            self.assertIn("openProcessMaterialConfirm", script)
            self.assertNotIn("confirm(", script)
            self.assertIn("from './process_material_import_policy.js'", script)
            self.assertIn("from './process_material_file_picker.js'", script)
            self.assertIn("setProcessMaterialImportBusyState", script)
            self.assertNotIn("function openModal(", script)
            self.assertIn("PROCESS_DOCUMENT_IMPORT_ACCEPT", script)
            self.assertIn("PROCESS_DOCUMENT_IMPORT_FORMAT_HINT", script)
            self.assertIn("setupProcessMaterialImportPicker({", script)
            self.assertIn("lp-import-selection__message", script)
            self.assertIn("data-selection-message", script)
            self.assertIn("filePicker.hasFiles()", script)
            self.assertIn("filePicker.getFiles().forEach", script)
            self.assertIn("filePicker.updateSubmitState();", script)
            self.assertIn("lp-import-selection", script)
            self.assertIn('role="status" aria-live="polite"', script)
            self.assertIn('-import-selection-message"', script)
            self.assertIn('aria-describedby=', script)
            self.assertIn("尚未选择文件，请先选择要导入解析的文件。", script)
            self.assertIn("if (submit.disabled) return;", script)
            self.assertIn("let importBusy = false;", script)
            self.assertIn("const setImportBusy = (overlay, busy) =>", script)
            self.assertIn("setProcessMaterialImportBusyState(overlay, importBusy);", script)
            self.assertIn("setImportBusy(overlay, true);", script)
            self.assertIn("setActionButtonBusy(submit, true, '正在解析…')", script)
            self.assertIn("setActionButtonBusy(submit, true, '正在解析…');\n                filePicker.updateSubmitState();", script)
            self.assertIn("close({ force: true });", script)
            self.assertIn("let createBusy = false;", script)
            self.assertIn("const setCreateBusy = (overlay, busy) =>", script)
            self.assertIn("if (createBusy || submit.disabled) return;", script)
            self.assertIn("setCreateBusy(overlay, true);", script)
            self.assertIn("setCreateBusy(overlay, false);", script)
            self.assertIn("canClose: () => !createBusy", script)
            self.assertIn("setActionButtonBusy(submit, true, '正在创建…')", script)
            self.assertIn("setProcessMaterialModalFormBusy", script)
            self.assertIn("let modalBusy = false;", script)
            self.assertIn("const setModalBusy = (overlay, busy) =>", script)
            self.assertIn("setActionButtonBusy(submit, true, '正在保存…')", script)
            self.assertIn("setModalBusy(overlay, true);", script)
            self.assertIn("setModalBusy(overlay, false);", script)
            self.assertIn("canClose: () => !modalBusy", script)
            self.assertIn("setActionButtonBusy(submit, false)", script)
            self.assertIn("setImportBusy(overlay, false);", script)
            self.assertIn("canClose: () => !importBusy", script)
            self.assertIn("prompt pool recording is best effort", script)
            self.assertIn("setActionButtonBusy(trigger, true, '正在继承…')", script)
            self.assertIn("setActionButtonBusy(trigger, true, '正在重试…')", script)
            self.assertIn("setActionButtonBusy(trigger, true, '正在删除…')", script)
            self.assertIn("setActionButtonBusy(trigger, false)", script)
            self.assertIn("refreshProcessMaterialActionList", script)
            self.assertIn("function showListRefreshWarning(err)", script)
            self.assertIn("grid.innerHTML = ''", script)
            self.assertLess(script.index("grid.innerHTML = ''"), script.index("grid.hidden = true;"))
            if "loadEvaluations" in script:
                self.assertIn("await refreshProcessMaterialActionList(trigger, loadEvaluations, showListRefreshWarning);", script)
            else:
                self.assertIn("await refreshProcessMaterialActionList(trigger, loadPlans, showListRefreshWarning);", script)
            self.assertIn("case 'delete':", script)
            self.assertIn(", btn); break;", script)
            self.assertIn(policy_attr, script)
            self.assertIn(label, script)

        for script in (assessment, teacher, lesson):
            self.assertIn("function progressText", script)
            self.assertIn("AI 正在解析导入文件…", script)
            self.assertNotIn("AI 正在准备…", script)
        self.assertIn("AI 正在根据课堂资料生成考核计划表…", assessment)
        self.assertIn("AI 正在根据试卷反推考核计划表…", assessment)
        self.assertIn("AI 正在根据课堂资料生成教师评学表…", teacher)
        self.assertNotIn("AI 正在归集班级表现…", teacher)
        self.assertIn("AI 正在按课次生成教案…", lesson)
        self.assertIn("AI 正在生成教案…", lesson)

        self.assertIn("export function setActionButtonBusy", action_state)
        self.assertIn("export async function refreshProcessMaterialActionList", action_state)
        self.assertIn("if (typeof refresh === 'function') await refresh();", action_state)
        self.assertIn("if (typeof onRefreshError === 'function') onRefreshError(err);", action_state)
        self.assertIn("button.dataset.actionBusy = 'true'", action_state)
        self.assertIn("button.disabled = true;", action_state)
        self.assertIn("button.disabled = false;", action_state)
        self.assertIn("export function setProcessMaterialModalFormBusy", action_state)
        self.assertIn("formSelector = '.lp-form'", action_state)
        self.assertIn("control.disabled = busyState;", action_state)
        self.assertIn("[data-pm-close], [data-lp-close], [data-ap-close], [data-te-close]", action_state)
        self.assertIn("export function openProcessMaterialModal", modal_helper)
        self.assertIn("export function openProcessMaterialConfirm", modal_helper)
        self.assertIn("onClose", modal_helper)
        self.assertIn("resolve(false)", modal_helper)
        self.assertIn("data-pm-confirm-ok", modal_helper)
        self.assertIn("data-pm-confirm-cancel", modal_helper)
        self.assertIn("DEFAULT_CLOSE_SELECTOR", modal_helper)
        self.assertIn("function trapModalFocus(event, overlay)", modal_helper)
        self.assertIn("function pickInitialFocusTarget(overlay)", modal_helper)
        self.assertIn("element.getClientRects().length > 0", modal_helper)
        self.assertIn("const autofocusTarget = focusable.find((element) => element.hasAttribute('autofocus'));", modal_helper)
        self.assertIn("const bodyTarget = focusable.find((element) => body?.contains(element));", modal_helper)
        self.assertIn("return focusable.find((element) => footer?.contains(element)) || focusable[0] || null;", modal_helper)
        self.assertIn("event.key !== 'Tab'", modal_helper)
        self.assertIn("last.focus({ preventScroll: true });", modal_helper)
        self.assertIn("first.focus({ preventScroll: true });", modal_helper)
        self.assertIn("if (e.key === 'Tab') trapModalFocus(e, overlay);", modal_helper)
        self.assertIn("document.removeEventListener('keydown', onKeydown);", modal_helper)
        self.assertIn("document.addEventListener('keydown', onKeydown);", modal_helper)
        self.assertIn("if (closed) return;", modal_helper)
        self.assertIn("canClose", modal_helper)
        self.assertIn("const force = Boolean(options?.force);", modal_helper)
        self.assertIn("if (!force && typeof canClose === 'function' && canClose() === false) return;", modal_helper)
        self.assertIn("const previousFocus = document.activeElement", modal_helper)
        self.assertIn("previousFocus.focus({ preventScroll: true })", modal_helper)
        self.assertIn("const FOCUSABLE_SELECTOR", modal_helper)
        self.assertLess(
            modal_helper.index("const bodyTarget = focusable.find((element) => body?.contains(element));"),
            modal_helper.index("return focusable.find((element) => footer?.contains(element)) || focusable[0] || null;"),
        )
        self.assertIn("const focusTarget = pickInitialFocusTarget(overlay);", modal_helper)
        self.assertIn("focusTarget.focus({ preventScroll: true })", modal_helper)
        tree_confirm = tree_modal[tree_modal.index("async function handleConfirm"):tree_modal.index("treeEl.addEventListener")]
        self.assertIn("submitting: false", tree_modal)
        self.assertIn("const close = ({ force = false } = {}) =>", tree_modal)
        self.assertIn("if (state.submitting && !force) return;", tree_modal)
        self.assertIn("function setSubmitting(submitting, btn)", tree_modal)
        self.assertIn("overlay.classList.toggle('is-submitting', state.submitting);", tree_modal)
        self.assertIn("overlay.setAttribute('aria-busy'", tree_modal)
        self.assertIn("closeBtn.disabled = state.submitting;", tree_modal)
        self.assertIn("treeEl.querySelectorAll('button').forEach", tree_modal)
        self.assertIn("panelEl.querySelectorAll('input, select, textarea, button').forEach", tree_modal)
        self.assertIn("if (!state.selectedNode || btn.disabled || state.submitting) return;", tree_modal)
        self.assertIn("setSubmitting(true, btn);", tree_confirm)
        self.assertIn("setSubmitting(false, btn);", tree_confirm)
        self.assertIn("close({ force: true });", tree_confirm)
        self.assertIn("if (state.submitting) return;", tree_modal)
        self.assertIn("try { await recordPromptForInput(promptInput, state.prompt); } catch (_) { /* prompt pool recording is best effort */ }", tree_modal)
        self.assertLess(
            tree_confirm.index("try { await recordPromptForInput(promptInput, state.prompt); } catch (_) { /* prompt pool recording is best effort */ }"),
            tree_confirm.index("close({ force: true });"),
        )

        self.assertIn(".lp-import-policy", styles)
        self.assertIn(".lp-filelist__main", styles)
        self.assertIn(".lp-filelist__main { flex: 1 1 auto;", styles)
        self.assertIn(".lp-import-selection", styles)
        self.assertIn(".lp-import-selection.is-ready", styles)
        self.assertIn(".lp-import-selection__message", styles)
        self.assertIn(".lp-import-selection__clear", styles)
        self.assertIn(".lp-dropzone.is-disabled", styles)
        self.assertIn(".lp-import.is-submitting", styles)
        self.assertIn(".lp-modal__close:disabled", styles)
        self.assertIn(".tsf-overlay.is-submitting", styles)
        self.assertIn(".tsf-close:disabled", styles)
        dropzone_disabled = re.search(r"\.lp-dropzone\.is-disabled \{[^}]+\}", styles)
        self.assertIsNotNone(dropzone_disabled)
        self.assertNotIn("pointer-events: none", dropzone_disabled.group(0))
        self.assertIn(".lp-filelist .lp-link:disabled", styles)
        self.assertIn("asset_url('js/manage_assessment_plans.js')", assessment_template)
        self.assertNotIn("manage_assessment_plans.js') }}?v=", assessment_template)
        self.assertIn("asset_url('js/manage_teacher_evaluations.js')", teacher_template)
        self.assertNotIn("manage_teacher_evaluations.js') }}?v=", teacher_template)

        for router_source, document_label in (
            (assessment_router, "考核计划表"),
            (teacher_router, "教师评学表"),
            (lesson_router, "教案"),
        ):
            marker = f'validate_process_document_import_filename(name, document_label="{document_label}")'
            self.assertNotIn("_ALLOWED_IMPORT_EXT", router_source)
            self.assertNotIn("_MAX_IMPORT_FILES", router_source)
            self.assertNotIn("if not files:", router_source)
            self.assertIn("validate_process_document_import_file_count(files)", router_source)
            self.assertIn("normalize_process_import_filename(upload.filename", router_source)
            self.assertIn(marker, router_source)
            self.assertIn("validate_process_document_import_file_bytes(data, filename=name)", router_source)
            self.assertNotIn("if not data:\n            continue", router_source)
            self.assertLess(
                router_source.index("validate_process_document_import_file_count(files)"),
                router_source.index("staged: list[dict[str, Any]] = []"),
            )
            self.assertLess(router_source.index(marker), router_source.index("data = await upload.read()"))
            self.assertLess(
                router_source.index("data = await upload.read()"),
                router_source.index("validate_process_document_import_file_bytes(data, filename=name)"),
            )
            self.assertLess(
                router_source.index("validate_process_document_import_file_bytes(data, filename=name)"),
                router_source.index('staged.append({"name": name, "data": data})'),
            )
            self.assertLess(router_source.index("if not staged:"), router_source.index("temp_dir = tempfile.mkdtemp"))

    def test_independent_process_import_cards_expose_source_quality_summary(self):
        summary = build_process_import_summary(
            {
                "source_type": "import",
                "status": "ready",
                "ai_gen_status": "completed_with_fallback",
                "ai_gen_error": "",
                "import_preview": {
                    "source_files": ["assessment-plan.docx", "rubric.pdf"],
                    "warnings": ["原文考核项分值合计为 90，未达到 100，请核对原始分值。"],
                },
            }
        )
        self.assertTrue(summary["visible"])
        self.assertEqual(summary["source_heading"], "导入来源")
        self.assertEqual(summary["source_file_label"], "assessment-plan.docx 等 2 个文件")
        self.assertEqual(summary["source_file_title"], "assessment-plan.docx、rubric.pdf")
        self.assertEqual(summary["quality_key"], "needs_review")
        self.assertEqual(summary["quality_label"], "需核对 1 项")
        self.assertEqual(summary["more_warning_count"], 0)

        many_warning_summary = build_process_import_summary(
            {
                "source_type": "import",
                "status": "ready",
                "ai_gen_status": "completed_with_fallback",
                "import_preview": {
                    "source_files": ["lesson-plan.pdf"],
                    "warnings": ["缺少课次主题", "缺少小结", "缺少作业", "PBL 表格不完整"],
                },
            }
        )
        self.assertEqual(many_warning_summary["warning_count"], 4)
        self.assertEqual(len(many_warning_summary["warnings"]), 3)
        self.assertEqual(
            many_warning_summary["all_warnings"],
            ["缺少课次主题", "缺少小结", "缺少作业", "PBL 表格不完整"],
        )
        self.assertEqual(many_warning_summary["more_warning_count"], 1)

        failed_summary = build_process_import_summary(
            {
                "source_type": "import",
                "status": "failed",
                "ai_gen_status": "failed",
                "ai_gen_error": "OCR 无法识别正文；文件可能为空",
                "import_preview": {"source_files": ["teacher-evaluation.png"], "warnings": []},
            }
        )
        self.assertEqual(failed_summary["quality_key"], "failed")
        self.assertEqual(failed_summary["quality_label"], "解析失败")
        self.assertEqual(failed_summary["warnings"], ["OCR 无法识别正文", "文件可能为空"])
        self.assertEqual(failed_summary["action_label"], "重新上传文件")

        classroom_failed_summary = build_process_import_summary(
            {
                "source_type": "classroom",
                "class_offering_id": 42,
                "status": "failed",
                "ai_gen_status": "failed",
                "ai_gen_error": "课堂资料不足，生成中断",
                "import_preview": {"source_files": [], "warnings": []},
            }
        )
        self.assertEqual(classroom_failed_summary["quality_key"], "failed")
        self.assertEqual(classroom_failed_summary["quality_label"], "生成失败")
        self.assertEqual(classroom_failed_summary["action_label"], "一键重试")
        self.assertEqual(classroom_failed_summary["source_heading"], "生成结果")

        failed_with_existing_warnings = build_process_import_summary(
            {
                "source_type": "import",
                "status": "failed",
                "ai_gen_status": "failed",
                "ai_gen_error": "OCR 无法识别正文；文件可能为空",
                "import_preview": {
                    "source_files": ["teacher-evaluation.png"],
                    "warnings": ["文件可能为空", "原图分辨率过低"],
                },
            }
        )
        self.assertEqual(
            failed_with_existing_warnings["warnings"],
            ["OCR 无法识别正文", "文件可能为空", "原图分辨率过低"],
        )
        self.assertEqual(
            failed_with_existing_warnings["all_warnings"],
            ["OCR 无法识别正文", "文件可能为空", "原图分辨率过低"],
        )
        self.assertEqual(failed_with_existing_warnings["warning_count"], 3)

        generating_summary = build_process_import_summary(
            {
                "source_type": "classroom",
                "status": "generating",
                "ai_gen_status": "pending",
                "import_preview": {"source_files": [], "warnings": []},
            }
        )
        self.assertTrue(generating_summary["visible"])
        self.assertEqual(generating_summary["source_heading"], "生成结果")
        self.assertEqual(generating_summary["quality_key"], "in_progress")
        self.assertEqual(generating_summary["quality_label"], "生成中")
        self.assertEqual(generating_summary["action_label"], "生成完成后可核对并导出")

        generated_warning_summary = build_process_import_summary(
            {
                "source_type": "classroom",
                "status": "ready",
                "ai_gen_status": "completed_with_fallback",
                "ai_gen_error": "AI 生成不可用，已使用本地草稿模板",
                "import_preview": {"source_files": [], "warnings": []},
            }
        )
        self.assertTrue(generated_warning_summary["visible"])
        self.assertEqual(generated_warning_summary["source_heading"], "生成结果")
        self.assertEqual(generated_warning_summary["source_file_label"], "生成结果")
        self.assertEqual(generated_warning_summary["quality_key"], "needs_review")
        self.assertEqual(generated_warning_summary["warnings"], ["AI 生成不可用，已使用本地草稿模板"])

        legacy_ready = schema_lesson_plans._SCHEMA_READY
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE lesson_plans (
                    id TEXT PRIMARY KEY,
                    teacher_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT '教案',
                    scope_level TEXT NOT NULL DEFAULT 'private',
                    status TEXT NOT NULL DEFAULT 'draft',
                    ai_gen_task_id TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    college TEXT NOT NULL DEFAULT '',
                    department TEXT NOT NULL DEFAULT ''
                )
                """
            )
            schema_lesson_plans._SCHEMA_READY = False
            schema_lesson_plans.ensure_lesson_plan_schema(conn)
            columns = {row[1] for row in conn.execute('PRAGMA table_info("lesson_plans")').fetchall()}
            self.assertIn("import_preview_json", columns)
        finally:
            conn.close()
            schema_lesson_plans._SCHEMA_READY = legacy_ready

        summary_helper = Path("classroom_app/services/process_material_import_summary_service.py").read_text(encoding="utf-8")
        lesson_schema = Path("classroom_app/db/schema_lesson_plans.py").read_text(encoding="utf-8")
        lesson_import = Path("classroom_app/services/lesson_plan_import_service.py").read_text(encoding="utf-8")
        assessment_import = Path("classroom_app/services/assessment_plan_import_service.py").read_text(encoding="utf-8")
        teacher_import = Path("classroom_app/services/teacher_evaluation_import_service.py").read_text(encoding="utf-8")
        assessment_service = Path("classroom_app/services/assessment_plan_service.py").read_text(encoding="utf-8")
        teacher_service = Path("classroom_app/services/teacher_evaluation_service.py").read_text(encoding="utf-8")
        lesson_service = Path("classroom_app/services/lesson_plan_service.py").read_text(encoding="utf-8")
        assessment_router = Path("classroom_app/routers/assessment_plans.py").read_text(encoding="utf-8")
        teacher_router = Path("classroom_app/routers/teacher_evaluations.py").read_text(encoding="utf-8")
        lesson_router = Path("classroom_app/routers/lesson_plans.py").read_text(encoding="utf-8")
        assessment_script = Path("static/js/manage_assessment_plans.js").read_text(encoding="utf-8")
        teacher_script = Path("static/js/manage_teacher_evaluations.js").read_text(encoding="utf-8")
        lesson_script = Path("static/js/manage_lesson_plans.js").read_text(encoding="utf-8")
        render_helper = Path("static/js/process_material_import_summary.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn("def build_process_import_summary", summary_helper)
        self.assertIn('"source_heading"', summary_helper)
        self.assertIn('source_heading = "导入来源" if is_import else "生成结果"', summary_helper)
        self.assertIn("def _process_verb", summary_helper)
        self.assertIn('return "解析" if source_type == "import" else "生成"', summary_helper)
        self.assertIn("quality_label = _busy_quality_label(source_type)", summary_helper)
        self.assertIn("quality_label = _failed_quality_label(source_type)", summary_helper)
        self.assertIn('is_busy = status in {"parsing", "generating"} or ai_status in {"pending", "running"}', summary_helper)
        self.assertIn('is_failed = status == "failed" or ai_status == "failed"', summary_helper)
        self.assertIn("if not is_import and not source_files and not warnings and not error_warnings and not is_busy and not is_failed:", summary_helper)
        self.assertIn("def _merge_warnings", summary_helper)
        self.assertIn("warnings = _merge_warnings(error_warnings, warnings)", summary_helper)
        self.assertIn("def _source_file_title", summary_helper)
        self.assertIn('"source_file_title"', summary_helper)
        self.assertIn('"more_warning_count"', summary_helper)
        self.assertIn('"all_warnings"', summary_helper)
        self.assertIn("ADD COLUMN IF NOT EXISTS import_preview_json", lesson_schema)
        self.assertIn("row[\"import_preview\"] = _load(row.get(\"import_preview_json\"), {})", lesson_service)
        self.assertIn("import_preview=import_preview", lesson_import)
        self.assertIn("completed_with_fallback\" if warnings else \"completed", lesson_import)

        for service_source in (assessment_service, teacher_service, lesson_service):
            self.assertIn("build_process_import_summary(row)", service_source)
            self.assertIn('"import_summary"', service_source)

        for import_source in (assessment_import, teacher_import, lesson_import):
            self.assertIn('"source_files": [item.get("name") for item in files]', import_source)
            self.assertIn('"warnings": warnings', import_source)
        self.assertIn('"source_files": [item.get("name") for item in saved]', lesson_router)
        for router_source in (assessment_router, teacher_router, lesson_router):
            self.assertIn('import_preview={"source_files": [], "warnings": []}', router_source)

        for script_source in (assessment_script, teacher_script, lesson_script):
            self.assertIn("from './process_material_import_summary.js'", script_source)
            self.assertIn("const importSummary = renderProcessImportSummary", script_source)
            self.assertIn("${importSummary}", script_source)

        self.assertIn("export function renderProcessImportSummary", render_helper)
        self.assertIn("function actionForSummary", render_helper)
        self.assertIn("qualityKey === 'failed'", render_helper)
        self.assertIn("if (!item?.can_manage) return null;", render_helper)
        self.assertIn("item.source_type === 'import') return 'import-again';", render_helper)
        self.assertIn("item.source_type === 'classroom' && item.class_offering_id) return 'retry';", render_helper)
        self.assertIn("qualityKey === 'needs_review') return item.can_manage ? 'edit' : 'preview';", render_helper)
        self.assertIn("function actionTextForSummary", render_helper)
        self.assertIn("if (qualityKey === 'failed')", render_helper)
        self.assertIn("if (!item?.can_manage) return '来源教师需处理';", render_helper)
        self.assertIn("if (action === 'retry') return '一键重试';", render_helper)
        self.assertIn("if (action === 'import-again') return '重新上传文件';", render_helper)
        self.assertIn("summary.action_label || '请处理失败记录'", render_helper)
        self.assertIn("来源教师需处理", render_helper)
        self.assertIn("qualityKey === 'needs_review' && action === 'preview' && !item?.can_manage", render_helper)
        self.assertIn("先预览核对", render_helper)
        self.assertIn("function moreWarningTextForSummary", render_helper)
        self.assertIn("const nextStep = action === 'edit' ? '请进入编辑器核对。' : '请先预览核对。';", render_helper)
        self.assertIn("${escapeHtml(moreWarningText)}", render_helper)
        self.assertIn("const allWarnings = asArray(summary.all_warnings || summary.warnings);", render_helper)
        self.assertIn("const fullWarningDetails = allWarnings.length > warnings.length", render_helper)
        self.assertIn("lp-import-summary__details", render_helper)
        self.assertIn("查看全部 ${allWarnings.length} 项核对点", render_helper)
        self.assertIn("${allWarnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}", render_helper)
        self.assertIn("qualityKey === 'ready'", render_helper)
        self.assertIn('data-action="${escapeHtml(action)}"', render_helper)
        self.assertIn('data-id="${escapeHtml(String(item.id))}"', render_helper)
        self.assertIn("const actionLabel = `${actionText}：${sourceTitle}`;", render_helper)
        self.assertIn('aria-label="${escapeHtml(actionLabel)}"', render_helper)
        self.assertIn("summary.source_heading || '导入来源'", render_helper)
        self.assertIn("${escapeHtml(sourceHeading)}", render_helper)
        self.assertIn("summary.source_file_title", render_helper)
        self.assertIn('title="${escapeHtml(sourceTitle)}"', render_helper)
        self.assertIn("lp-import-summary__warnings", render_helper)
        self.assertIn("lp-import-summary__more", render_helper)
        self.assertIn("lp-import-summary__action", render_helper)
        self.assertIn(".lp-import-summary", styles)
        self.assertIn(".lp-import-summary__action", styles)
        self.assertIn("button.lp-import-summary__action", styles)
        self.assertIn("button.lp-import-summary__action:focus-visible", styles)
        self.assertIn(".lp-import-summary__more", styles)
        self.assertIn(".lp-import-summary__details", styles)
        self.assertIn(".lp-import-summary__details summary", styles)
        self.assertIn(".lp-import-summary__details ol", styles)
        self.assertIn(".lp-status.is-warning", styles)

    def test_manage_process_material_filters_use_responsive_grid(self):
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        manifest = json.loads(Path("static/vendor/manifest.json").read_text(encoding="utf-8"))
        lesson_template = Path("templates/manage/lesson_plans.html").read_text(encoding="utf-8")
        lesson_script = Path("static/js/manage_lesson_plans.js").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: minmax(220px, min(32%, 390px)) minmax(0, 1fr)", styles)
        self.assertIn("repeat(auto-fit, minmax(min(132px, 100%), 1fr))", styles)
        self.assertIn(".manage-lp__filters { display: grid;", styles)
        self.assertIn("min-width: 0; max-width: 100%;", styles)
        self.assertNotIn("repeat(6, minmax(116px, 1fr)) auto", styles)
        self.assertEqual(manifest["tailwind_app"]["path"], "css/tailwind-app.css")
        self.assertNotIn("version", manifest["tailwind_app"])
        for attr in (
            "data-lp-filter-school",
            "data-lp-filter-college",
            "data-lp-filter-course",
            "data-lp-filter-class",
            "data-lp-tags",
            "data-lp-active-filters",
            "data-lp-clear-filters",
        ):
            self.assertIn(attr, lesson_template)
        self.assertIn("from './process_material_filters.js'", lesson_script)
        self.assertIn("selectedTags: new Set()", lesson_script)
        self.assertIn("function renderFilterState()", lesson_script)
        self.assertIn("root.querySelector('[data-lp-filter-school]')", lesson_script)
        self.assertIn("root.querySelector('[data-lp-tags]').addEventListener('click'", lesson_script)
        self.assertIn("const tag = normalizeFacetValue(btn.dataset.lpTagFilter)", lesson_script)
        self.assertIn("hasMatchingSelectedTag(plan.tags, state.selectedTags)", lesson_script)
        self.assertIn("case 'sessions_asc'", lesson_script)

    def test_manage_ai_import_modal_uses_type_aware_file_format_guidance(self):
        template = Path("templates/manage/materials.html").read_text(encoding="utf-8")
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn('id="materials-ai-import-format-hint"', template)
        self.assertIn("aiImportFormatHint", script)
        self.assertIn("function getSelectedAiImportTypeMeta()", script)
        self.assertIn("function updateAiImportFormatGuide", script)
        self.assertIn("refs.aiImportFileInput.setAttribute('accept'", script)
        self.assertIn("getAiImportFormatMismatchMessage", script)
        self.assertIn("function setModalDismissDisabled", script)
        self.assertIn("modal?.querySelectorAll('[data-dismiss=\"modal\"]')", script)
        self.assertIn("button.setAttribute('aria-disabled', disabled ? 'true' : 'false');", script)
        self.assertIn("setModalDismissDisabled(refs.aiImportModal, busy);", script)
        self.assertIn("function bindBusyModalCloseGuard", script)
        self.assertIn("event.stopImmediatePropagation();", script)
        self.assertIn("文件正在上传并加入解析队列，请等待完成。", script)
        self.assertIn("bindAiWorkModalCloseGuards();", script)
        self.assertIn("renderAiImportTypes({ preserveStatus: false })", script)
        self.assertIn("refs.aiImportType?.addEventListener('change'", script)
        self.assertLess(
            script.index("!isAiImportFileAccepted(state.aiImport.file)"),
            script.index("formData.append('file', state.aiImport.file, state.aiImport.file.name)"),
        )
        import_submit_block = script[
            script.index("async function submitAiImport()"):
            script.index("function getAiGenerateAttachmentCount()")
        ]
        self.assertIn("setAiImportStatus(error.message || 'AI 解析导入失败', 'error');", import_submit_block)
        self.assertNotIn("throw error;", import_submit_block)
        import_listener_block = script[
            script.index("const aiImportSubmit = event.target.closest('#materials-ai-import-submit-btn');"):
            script.index("const aiGenerateSubmit = event.target.closest('#materials-ai-generate-submit-btn');")
        ]
        self.assertIn("if (aiImportSubmit.disabled) return;", import_listener_block)
        self.assertIn("submitAiImport().catch", import_listener_block)
        self.assertIn("setAiImportStatus(error.message || 'AI 解析导入失败', 'error');", import_listener_block)
        self.assertNotIn("showToast(error.message || 'AI 解析导入失败'", import_listener_block)
        self.assertIn(".materials-ai-import-format-hint", styles)
        self.assertRegex(
            styles,
            r"\.modal-dialog\s*\{[^}]*pointer-events:\s*auto;",
        )

    def test_materials_manage_delete_uses_process_material_confirm(self):
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        delete_block = script[
            script.index("async function deleteActiveMaterial()"):
            script.index("function formatRepositoryCommandPreview")
        ]
        overlay_z_index = re.search(r"\.lp-modal-overlay\s*\{[^}]*z-index:\s*(\d+)", styles)
        detail_z_index = re.search(r"\.materials-detail-modal\s*\{\s*z-index:\s*(\d+)", styles)

        self.assertIn("from './process_material_modal.js'", script)
        self.assertIn("openProcessMaterialConfirm({", delete_block)
        self.assertIn("title: '删除材料'", delete_block)
        self.assertIn("detail: '删除后无法恢复，关联的过程材料预览、导出入口和课堂分配也会一并失效。'", delete_block)
        self.assertIn("confirmText: '删除'", delete_block)
        self.assertIn("tone: 'danger'", delete_block)
        self.assertNotIn("window.confirm", delete_block)
        self.assertIsNotNone(overlay_z_index)
        self.assertIsNotNone(detail_z_index)
        self.assertGreater(int(overlay_z_index.group(1)), int(detail_z_index.group(1)))

    def test_manage_generic_generation_does_not_expose_grade_records(self):
        html = Path("templates/manage/materials.html").read_text(encoding="utf-8")
        match = re.search(r'<select id="materials-ai-generate-type".*?</select>', html, re.S)
        self.assertIsNotNone(match)
        select_html = match.group(0)

        self.assertIn('value="assessment_plan"', select_html)
        self.assertIn('value="exam_paper"', select_html)
        self.assertIn('value="grading_rubric"', select_html)
        self.assertNotIn('value="ordinary_grade_record"', select_html)
        self.assertNotIn('value="exam_grade_record"', select_html)

    def test_grade_record_pages_preselect_import_and_block_generic_generation(self):
        for key in ("ordinary_grade_record", "exam_grade_record"):
            self.assertEqual(GRADE_RECORD_IMPORT_PRESETS[key]["document_group"], "final_material")
            self.assertEqual(GRADE_RECORD_IMPORT_PRESETS[key]["document_type"], key)
            self.assertTrue(GRADE_RECORD_GENERATE_BLOCKERS[key]["blocked"])
            self.assertEqual(GRADE_RECORD_GENERATE_BLOCKERS[key]["document_type"], key)
            self.assertIn("Excel", GRADE_RECORD_GENERATE_BLOCKERS[key]["status"])

    def test_manage_ai_generate_button_reuses_process_material_preset(self):
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        self.assertIn("const initialAiGeneratePreset = getInitialAiGeneratePreset();", script)
        self.assertIn("openAiGenerateModal(initialAiGeneratePreset);", script)

    def test_assessment_plan_generation_uses_tree_select_field_contract(self):
        script = Path("static/js/manage_assessment_plans.js").read_text(encoding="utf-8")
        teacher_script = Path("static/js/manage_teacher_evaluations.js").read_text(encoding="utf-8")
        offering_tree = Path("static/js/process_material_offering_tree.js").read_text(encoding="utf-8")
        template = Path("templates/manage/assessment_plans.html").read_text(encoding="utf-8")
        router = Path("classroom_app/routers/assessment_plans.py").read_text(encoding="utf-8")
        generator = Path("classroom_app/services/assessment_plan_generation_service.py").read_text(encoding="utf-8")
        page = Path("classroom_app/routers/ui_parts/assessment_plan_pages.py").read_text(encoding="utf-8")

        self.assertIn("asset_url('js/manage_assessment_plans.js')", template)
        self.assertNotIn("manage_assessment_plans.js') }}?v=", template)
        self.assertIn("from './tree_select_form_modal.js'", script)
        self.assertIn("from './process_material_offering_tree.js'", script)
        self.assertIn("export function buildProcessMaterialOfferingTree", offering_tree)
        self.assertIn("export function formatProcessMaterialOfferingOptionLabel", offering_tree)
        self.assertIn("export function getProcessMaterialClassDisplayName", offering_tree)
        self.assertIn("getProcessMaterialSemesterSortValue", offering_tree)
        self.assertIn("(?:^|[^\\d])2(?:[^\\d]|$)", offering_tree)
        for page_script in (script, teacher_script):
            self.assertIn("from './process_material_offering_tree.js'", page_script)
            self.assertIn("buildProcessMaterialOfferingTree(state.offerings)", page_script)
            self.assertIn("getProcessMaterialClassDisplayName(offering)", page_script)
            self.assertIn("formatProcessMaterialOfferingOptionLabel", page_script)
            self.assertNotIn("function buildOfferingTree()", page_script)
            self.assertNotIn("function semesterSortValue(", page_script)
            self.assertNotIn("function classDisplayName(", page_script)
        self.assertIn("formatProcessMaterialOfferingOptionLabel(o, { includeSemester: true })", teacher_script)
        self.assertIn("function offeringPanelDescriptor(offering)", script)
        self.assertIn("openTreeSelectFormModal({", script)
        self.assertIn("promptPoolKey: 'assessment_plan.generate_from_classroom'", script)
        self.assertIn("fields: fieldValues || {}", script)
        self.assertIn("return false;", script)

        self.assertIn("_GENERATE_FIELD_KEYS = set(ap.FIELD_KEYS)", router)
        self.assertIn("def _clean_generate_field_overrides", router)
        self.assertIn("field_overrides = _clean_generate_field_overrides(body.get(\"fields\"))", router)
        self.assertIn("fields.update(field_overrides)", router)
        self.assertIn("field_overrides=field_overrides", router)
        generate_route = router[
            router.index("async def generate_from_classroom"):
            router.index("@router.post(\"/import\"")
        ]
        self.assertLess(
            generate_route.index("fields.update(field_overrides)"),
            generate_route.index("ap.create_assessment_plan("),
        )

        self.assertIn("field_overrides: dict[str, Any] | None = None", generator)
        self.assertIn("def _clean_field_overrides", generator)
        self.assertIn("offering_fields.update(_clean_field_overrides(field_overrides))", generator)
        self.assertLess(
            generator.index("offering_fields.update(_clean_field_overrides(field_overrides))"),
            generator.index("_user_prompt(offering_fields, classroom_context, prompt)"),
        )

        self.assertIn("sem.start_date AS semester_start_date", page)
        self.assertIn("ORDER BY sem.start_date DESC, co.name, c.name", page)

    def test_lesson_plan_generation_uses_planner_not_legacy_dropdown(self):
        script = Path("static/js/manage_lesson_plans.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn("function openGeneratePlannerModal()", script)
        self.assertIn("data-gen-session-list", script)
        self.assertIn("payloadSessions(plan)", script)
        self.assertIn("submitting: false", script)
        self.assertIn("function applyPlannerInteractiveState(overlay, canSubmit)", script)
        self.assertIn("plannerRoot?.classList.toggle('is-submitting', planner.submitting);", script)
        self.assertIn("plannerRoot?.setAttribute('aria-busy'", script)
        self.assertIn("overlay.querySelectorAll('[data-lp-close], [data-pm-close]').forEach", script)
        self.assertIn("overlay.querySelectorAll('[data-gen-main] input, [data-gen-main] select, [data-gen-main] textarea, [data-gen-main] button').forEach", script)
        self.assertIn("card.draggable = !planner.submitting;", script)
        self.assertIn("if (planner.submitting) return;", script)
        self.assertIn("if (actionBtn.disabled) return;", script)
        self.assertIn("setActionButtonBusy(actionBtn, true, '正在新增…')", script)
        self.assertIn("setActionButtonBusy(submit, true, '正在生成…')", script)
        self.assertIn("planner.submitting = true;", script)
        self.assertIn("close({ force: true });", script)
        self.assertIn("planner.submitting = false;", script)
        self.assertIn("canClose: () => !planner.submitting", script)
        self.assertIn("try { await recordPromptForInput(promptEl, prompt); } catch (_) { /* prompt pool recording is best effort */ }", script)
        self.assertIn("try { await recordPromptPoolInputs(overlay); } catch (_) { /* prompt pool recording is best effort */ }", script)
        self.assertIn("openGeneratePlannerModal();", script)
        self.assertIn("apiFetch(`/api/lesson-plans/classroom/${offeringId}/generation-plan`)", script)
        submit_flow = script[
            script.index("overlay.querySelector('[data-gen-submit]').addEventListener"):
            script.index("canClose: () => !planner.submitting")
        ]
        self.assertLess(submit_flow.index("planner.submitting = true;"), submit_flow.index("setActionButtonBusy(submit, true"))
        self.assertLess(submit_flow.index("setActionButtonBusy(submit, false);"), submit_flow.index("planner.submitting = false;"))
        self.assertIn(".lp-gen-planner.is-submitting", styles)
        self.assertIn("cursor: wait", styles)
        self.assertNotIn("function openGenerateModal()", script)
        self.assertNotIn("data-lp-form-generate", script)

    def test_grade_record_manage_pages_offer_classroom_generate_and_import_shortcuts(self):
        template = Path("templates/manage/materials.html").read_text(encoding="utf-8")
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")

        self.assertIn("process_generate_blocked", template)
        self.assertRegex(template, r"\{% block content %\}\s*\{% set process_generate_blocked")
        self.assertIn("materials-classroom-generate-open-btn", template)
        self.assertIn("materials-ai-import-shortcut-btn", template)
        self.assertIn("data-process-classroom-generate", template)
        self.assertIn("data-process-ai-import", template)
        self.assertIn("materials-classroom-generate-modal", template)
        self.assertIn("__LANSHARE_MATERIALS_MANAGE_PAGE_CONTROLLER__", template)
        self.assertIn("asset_url('js/materials_manage.js')", template)
        self.assertIn("getProcessGeneratePolicy", script)
        self.assertIn("openClassroomGenerateModal", script)
        self.assertIn("openAiImportModal(initialAiImportPreset)", script)
        self.assertIn("open_final_material", script)
        self.assertIn("final_material_type", script)

    def test_ordinary_grade_manage_generation_stays_on_page_and_highlights_result(self):
        template = Path("templates/manage/materials.html").read_text(encoding="utf-8")
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn('id="materials-ordinary-grade-wizard"', template)
        self.assertEqual(template.count("data-materials-ordinary-step-index="), 4)
        self.assertEqual(template.count("data-materials-ordinary-progress-step="), 5)
        self.assertIn('id="materials-ordinary-grade-prompt"', template)
        self.assertIn("function openManageOrdinaryGradeWizard", script)
        self.assertIn("/ordinary-grade-record/candidates", script)
        self.assertIn("/final-materials/generate", script)
        self.assertIn("document_type: 'ordinary_grade_record'", script)
        self.assertIn("new Set(selectedIds).size !== 4", script)
        self.assertIn("function revealRecentlyGeneratedMaterial", script)
        self.assertIn("package_material_id", script)
        self.assertIn("recentGeneratedHighlightArmed", script)
        self.assertIn("is-generated-highlight", script)
        self.assertNotIn("function classroomGenerateUrl(", script)
        self.assertIn(".materials-manage-row.is-generated-highlight", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)

    def test_ordinary_grade_material_attributes_include_business_context(self):
        router = Path("classroom_app/routers/materials_parts/library.py").read_text(encoding="utf-8")
        helper = Path("classroom_app/routers/materials_parts/ai_import_helpers.py").read_text(encoding="utf-8")
        modes = Path("static/js/base_resource_modes.js").read_text(encoding="utf-8")

        for key in ("academic_year", "semester", "course_name", "class_name", "teacher_name", "export_filename"):
            self.assertIn(f'"{key}"', router)
            self.assertIn(f"key: '{key}'", modes)
        self.assertIn('"academic_year": "学年"', helper)
        self.assertIn("readOnlyFields", modes)

    def test_classroom_final_material_url_auto_opens_selected_type(self):
        script = Path("static/js/classroom_materials.js").read_text(encoding="utf-8")

        self.assertIn("const FINAL_MATERIAL_TYPES", script)
        self.assertIn("ordinary_grade_record", script)
        self.assertIn("exam_grade_record", script)
        self.assertIn("function openFinalMaterialModal", script)
        self.assertIn("function openInitialFinalMaterialModalIfRequested", script)
        self.assertIn("open_final_material", script)
        self.assertIn("final_material_type", script)

    def test_ordinary_grade_record_candidates_split_homework_from_assessment(self):
        script = Path("static/js/classroom_materials.js").read_text(encoding="utf-8")
        template = Path("templates/classroom_main_v4.html").read_text(encoding="utf-8")
        island = Path("frontend/src/islands/classroom-page.tsx").read_text(encoding="utf-8")

        self.assertIn("function ordinaryGradeCandidateBuckets", script)
        self.assertIn("function isOrdinaryAssessmentCandidate", script)
        self.assertIn("buckets.homework.length < 3 || buckets.assessment.length < 1", script)
        self.assertIn("buckets.homework.length", script)
        self.assertIn("buckets.assessment.length", script)
        self.assertIn("function openOrdinaryGradePicker", script)
        self.assertIn("function selectOrdinaryGradeCandidate", script)
        self.assertIn("usedByOtherStep", script)
        self.assertIn('data-ordinary-grade-step-index="0"', template)
        self.assertIn('data-ordinary-grade-step-index="3"', template)
        self.assertIn('id="classroom-final-material-prompt-step"', template)
        self.assertIn("第 5 步", template)
        self.assertIn("ordinary-grade-floor-20260729", island)

    def test_ordinary_grade_floor_policy_is_teacher_controlled_and_auditable(self):
        script = Path("static/js/classroom_materials.js").read_text(encoding="utf-8")
        template = Path("templates/classroom_main_v4.html").read_text(encoding="utf-8")
        request_model = Path("classroom_app/routers/materials_parts/common.py").read_text(encoding="utf-8")
        router = Path("classroom_app/routers/materials_parts/final_materials.py").read_text(encoding="utf-8")
        service = Path("classroom_app/services/ordinary_grade_record_service.py").read_text(encoding="utf-8")

        self.assertIn('id="classroom-ordinary-score-floor-enabled"', template)
        self.assertIn('id="classroom-ordinary-score-floor"', template)
        self.assertIn("出勤率达到 70%", template)
        self.assertIn("minimum_ordinary_score_enabled", script)
        self.assertIn("minimum_ordinary_score", script)
        self.assertIn("minimum_ordinary_score_enabled: bool = True", request_model)
        self.assertIn("minimum_ordinary_score=payload.minimum_ordinary_score", router)
        self.assertIn("ORDINARY_GRADE_ATTENDANCE_ELIGIBILITY_PERCENT = 70.0", service)
        self.assertIn("balanced-deterministic-v1", service)
        self.assertIn('workbook.create_sheet("最低分配平审计")', service)

    def test_ordinary_grade_kind_override_is_visible_in_both_teacher_views(self):
        classroom_template = Path("templates/classroom_main_v4.html").read_text(encoding="utf-8")
        exam_template = Path("templates/manage/exams.html").read_text(encoding="utf-8")
        controls = Path("static/js/ordinary_grade_kind_controls.js").read_text(encoding="utf-8")
        service = Path("classroom_app/services/ordinary_grade_record_service.py").read_text(encoding="utf-8")

        self.assertIn("data-ordinary-grade-kind-select", classroom_template)
        self.assertIn("平时成绩用途", classroom_template)
        self.assertIn("paper.ordinary_grade_usages", exam_template)
        self.assertIn("data-ordinary-grade-kind-select", exam_template)
        self.assertIn("/ordinary-grade-kind", controls)
        self.assertIn("lanshare:ordinary-grade-kind-updated", controls)
        self.assertIn("ordinary_grade_kind_override", service)
        self.assertIn("不能放入平时作业", service)

    def test_classroom_generation_picker_supports_semester_filter_and_fuzzy_search(self):
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        template = Path("templates/manage/materials.html").read_text(encoding="utf-8")
        router = Path("classroom_app/routers/materials_parts/library.py").read_text(encoding="utf-8")

        self.assertIn('id="materials-classroom-semester-filter"', template)
        self.assertIn('id="materials-classroom-search"', template)
        self.assertIn("function populateClassroomSemesterFilter", script)
        self.assertIn("function fuzzyTextMatches", script)
        self.assertIn("offeringSemesterKey(offering)", script)
        self.assertIn("ordinary_homework_count", script)
        self.assertIn("ordinary_assessment_count", script)
        self.assertIn("补齐来源", script)
        self.assertIn("COALESCE(s.start_date, o.created_at) DESC", router)
        self.assertIn("classify_ordinary_grade_assignment", router)

    def test_classroom_grade_record_generation_disables_submit_until_sources_ready(self):
        script = Path("static/js/classroom_materials.js").read_text(encoding="utf-8")
        router = Path("classroom_app/routers/materials_parts/final_materials.py").read_text(encoding="utf-8")

        self.assertIn("function getOrdinaryGradeReadiness", script)
        self.assertIn("function getExamGradeReadiness", script)
        self.assertIn("function getFinalMaterialBlockingMessage", script)
        self.assertIn("function updateFinalMaterialSubmitState", script)
        self.assertIn("dom.finalMaterialSubmitBtn.disabled = disabled", script)
        self.assertIn("请先补齐来源", script)
        self.assertIn("ordinaryGradeCandidatesLoading", script)
        self.assertIn("examGradeCandidatesLoading", script)
        self.assertIn("refreshOrdinaryGradeAvailabilityStatus", script)
        self.assertIn("refreshExamGradeAvailabilityStatus", script)
        self.assertIn("select?.addEventListener('change', refreshOrdinaryGradeAvailabilityStatus)", script)
        self.assertIn("dom.examGradeSelect?.addEventListener('change', refreshExamGradeAvailabilityStatus)", script)
        self.assertIn("min_refresh_interval_seconds=ORDINARY_GRADE_ATTENDANCE_CACHE_SECONDS", router)
        self.assertIn("get_classroom_smart_attendance_freshness", router)
        self.assertIn("没有找到能与当前课堂可靠对应的最新考勤数据", router)

    def test_classroom_exam_paper_and_rubric_generation_disables_submit_until_sources_ready(self):
        script = Path("static/js/classroom_materials.js").read_text(encoding="utf-8")
        router = Path("classroom_app/routers/materials_parts/final_materials.py").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn('/api/classrooms/{class_offering_id}/final-materials/prerequisites', router)
        self.assertIn("get_classroom_final_material_prerequisites", router)
        self.assertIn('document_type="assessment_plan"', router)
        self.assertIn('document_type="exam_paper"', router)
        self.assertIn('"source_record": assessment_plan_source', router)
        self.assertIn('"source_record": exam_paper_source', router)
        self.assertIn("请先在本课堂导入或生成“课程考核计划表”", router)
        self.assertIn("请先在本课堂导入或生成“课程考核试卷”", router)
        self.assertIn("finalMaterialPrerequisitesLoaded", script)
        self.assertIn("function compactDateTime", script)
        self.assertIn("function needsFinalMaterialPrerequisites", script)
        self.assertIn("function getFinalMaterialSourceReadiness", script)
        self.assertIn("function loadFinalMaterialPrerequisites", script)
        self.assertIn("loadFinalMaterialPrerequisites({ force: true })", script)
        self.assertIn("sourceMessage", script)
        self.assertIn("已关联${sourceLabel}", script)
        self.assertIn("生成评分细则时会按这份试卷逐题拆分给分点", script)
        self.assertIn("生成试卷时会继承这份计划表的考核形式", script)
        self.assertIn("setFinalMaterialStatus(readiness.sourceMessage, 'success')", script)
        self.assertIn("请等待系统确认当前课堂是否已有前置材料", script)
        self.assertIn("请先在本课堂导入或生成课程考核计划表", script)
        self.assertIn("请先在本课堂导入或生成课程考核试卷", script)
        self.assertIn('.classroom-final-material-status[data-status-kind="success"]', styles)
        self.assertIn('.classroom-final-material-status[data-status-kind="blocking"]', styles)
        self.assertIn('.classroom-final-material-status[data-status-kind="error"]', styles)

    def test_process_material_pdf_exports_are_exposed_for_word_form_types(self):
        helper = Path("classroom_app/routers/materials_parts/ai_import_helpers.py").read_text(encoding="utf-8")
        island = Path("frontend/src/islands/materials-manage-page.tsx").read_text(encoding="utf-8")
        library = Path("classroom_app/routers/materials_parts/library.py").read_text(encoding="utf-8")

        for document_type in ("assessment_plan", "exam_paper", "grading_rubric"):
            self.assertEqual(_material_ai_import_export_format(document_type), "docx")
            self.assertEqual(
                _material_ai_import_pdf_export_url(42, document_type),
                "/api/materials/ai-import-records/42/export?format=pdf",
            )
        for document_type in ("ordinary_grade_record", "exam_grade_record"):
            self.assertEqual(_material_ai_import_export_format(document_type), "xlsx")
            self.assertEqual(_material_ai_import_pdf_export_url(42, document_type), "")
        self.assertIn("FINAL_MATERIAL_SPREADSHEET_TYPES", helper)
        self.assertIn("_material_ai_import_pdf_export_url(record_id, document_type)", helper)
        self.assertIn("_build_ai_import_record_detail_payload(ai_import_record)", library)

    def test_process_preview_modals_offer_pdf_export_actions(self):
        assessment = Path("static/js/manage_assessment_plans.js").read_text(encoding="utf-8")
        teacher_evaluation = Path("static/js/manage_teacher_evaluations.js").read_text(encoding="utf-8")
        assessment_service = Path("classroom_app/services/assessment_plan_service.py").read_text(encoding="utf-8")
        teacher_service = Path("classroom_app/services/teacher_evaluation_service.py").read_text(encoding="utf-8")
        assessment_template = Path("templates/manage/assessment_plans.html").read_text(encoding="utf-8")
        teacher_template = Path("templates/manage/teacher_evaluations.html").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        renderer = Path("classroom_app/services/document_render_service.py").read_text(encoding="utf-8")

        self.assertIn("/api/assessment-plans/${id}/export?fmt=pdf", assessment)
        self.assertIn("/api/teacher-evaluations/${id}/export?fmt=pdf", teacher_evaluation)
        self.assertIn("status === 'ready' ? '预览就绪'", renderer)
        self.assertIn("function renderAssessmentPreviewExportActions(plan, id)", assessment)
        self.assertIn("plan?.score_balanced === false", assessment)
        self.assertIn("考核项分值合计为", assessment)
        self.assertIn("调整到 100 后才能导出", assessment)
        self.assertLess(
            assessment.index("plan?.score_balanced === false"),
            assessment.index("/api/assessment-plans/${id}/export?fmt=pdf"),
        )
        self.assertIn("function renderEvaluationPreviewExportActions(evaluation, id)", teacher_evaluation)
        self.assertIn("evaluation?.is_complete === false", teacher_evaluation)
        self.assertIn("评学表尚未填写完整", teacher_evaluation)
        self.assertLess(
            teacher_evaluation.index("evaluation?.is_complete === false"),
            teacher_evaluation.index("/api/teacher-evaluations/${id}/export?fmt=pdf"),
        )
        self.assertIn(".lp-btn--disabled", styles)
        self.assertIn(".lp-preview__notice", styles)
        self.assertIn("download_disabled_reason", renderer)
        self.assertIn("doc-preview-download.is-disabled", renderer)
        self.assertIn("doc-preview-download-note", renderer)
        self.assertIn("_assert_export_score_balanced(plan)", assessment_service)
        self.assertIn("download_disabled_reason=download_disabled_reason", assessment_service)
        self.assertIn("missing = missing_fields(evaluation)", teacher_service)
        self.assertIn("download_disabled_reason=download_disabled_reason", teacher_service)
        self.assertIn("asset_url('js/manage_assessment_plans.js')", assessment_template)
        self.assertNotIn("manage_assessment_plans.js') }}?v=", assessment_template)
        self.assertIn("asset_url('js/manage_teacher_evaluations.js')", teacher_template)
        self.assertNotIn("manage_teacher_evaluations.js') }}?v=", teacher_template)

    def test_failed_process_import_cards_reopen_upload_instead_of_retry_api(self):
        scripts = [
            Path("static/js/manage_lesson_plans.js").read_text(encoding="utf-8"),
            Path("static/js/manage_assessment_plans.js").read_text(encoding="utf-8"),
            Path("static/js/manage_teacher_evaluations.js").read_text(encoding="utf-8"),
        ]
        routers = [
            Path("classroom_app/routers/lesson_plans.py").read_text(encoding="utf-8"),
            Path("classroom_app/routers/assessment_plans.py").read_text(encoding="utf-8"),
            Path("classroom_app/routers/teacher_evaluations.py").read_text(encoding="utf-8"),
        ]
        services = [
            Path("classroom_app/services/lesson_plan_service.py").read_text(encoding="utf-8"),
            Path("classroom_app/services/assessment_plan_service.py").read_text(encoding="utf-8"),
            Path("classroom_app/services/teacher_evaluation_service.py").read_text(encoding="utf-8"),
        ]
        render_helper = Path("static/js/process_material_import_summary.js").read_text(encoding="utf-8")

        self.assertIn("if (item.source_type === 'import') return 'import-again';", render_helper)
        self.assertIn("if (item.source_type === 'classroom' && item.class_offering_id) return 'retry';", render_helper)
        self.assertIn('data-action="${escapeHtml(action)}"', render_helper)

        for script in scripts:
            failed_actions = script[script.index("function renderFailedActions"):script.index("function renderCard")]
            self.assertIn("function renderFailedActions", script)
            self.assertRegex(failed_actions, r"if \(!(plan|evaluation)\.can_manage\)")
            self.assertIn("来源教师需处理该失败记录", failed_actions)
            self.assertNotIn('data-action="import-again"', failed_actions)
            self.assertNotIn('data-action="retry"', failed_actions)
            self.assertIn('data-action="delete"', failed_actions)
            self.assertIn("const importSummary = renderProcessImportSummary", script)
            self.assertIn("${importSummary}", script)
            self.assertIn("function renderImportRetryNote", script)
            self.assertIn("重新上传模式", script)
            self.assertIn("不会覆盖", script)
            self.assertIn("原失败记录仍保留，可稍后删除", script)
            self.assertIn("const retryNote = renderImportRetryNote(retryingFailedId);", script)
            self.assertIn("case 'import-again': openImportModal({ retryingFailedId: id }); break;", script)
            self.assertIn("? '已重新上传并开始解析，原失败记录仍保留，可稍后删除。'", script)

        for router in routers:
            self.assertIn('elif source_type == "import":', router)
            self.assertIn("需重新上传文件再解析", router)
        for service in services:
            self.assertIn('"class_offering_id": row.get("class_offering_id")', service)
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        self.assertIn(".lp-import-retry-note", styles)
        self.assertIn(".lp-import-retry-note strong", styles)

    def test_process_material_generate_branches_do_not_reference_import_uploads(self):
        for router_path in (
            "classroom_app/routers/lesson_plans.py",
            "classroom_app/routers/assessment_plans.py",
            "classroom_app/routers/teacher_evaluations.py",
        ):
            source = Path(router_path).read_text(encoding="utf-8")
            generate_block = source[
                source.index('@router.post("/generate"') : source.index('@router.post("/import"')
            ]
            self.assertIn('import_preview={"source_files": [], "warnings": []}', generate_block)
            self.assertNotIn("saved", generate_block)

    def test_independent_process_material_pages_recover_stale_tasks_before_listing(self):
        assessment_page = Path("classroom_app/routers/ui_parts/assessment_plan_pages.py").read_text(encoding="utf-8")
        teacher_page = Path("classroom_app/routers/ui_parts/teacher_evaluation_pages.py").read_text(encoding="utf-8")
        lesson_router = Path("classroom_app/routers/lesson_plans.py").read_text(encoding="utf-8")
        assessment_router = Path("classroom_app/routers/assessment_plans.py").read_text(encoding="utf-8")
        teacher_router = Path("classroom_app/routers/teacher_evaluations.py").read_text(encoding="utf-8")
        recovery_service = Path("classroom_app/services/process_material_recovery_service.py").read_text(encoding="utf-8")
        lesson_recovery_service = Path("classroom_app/services/lesson_plan_recovery_service.py").read_text(encoding="utf-8")

        self.assertIn("def expire_stale_assessment_plan_tasks", recovery_service)
        self.assertIn("def expire_stale_teacher_evaluation_tasks", recovery_service)
        self.assertIn("status IN ('generating', 'parsing')", recovery_service)
        self.assertIn("teacher_id: int | None = None", recovery_service)
        self.assertIn("teacher_filter = \" AND teacher_id = ?\"", recovery_service)
        self.assertIn("source_type = 'import'", recovery_service)
        self.assertIn("重新上传文件再解析", recovery_service)
        self.assertIn("重试生成", recovery_service)
        self.assertIn("source_type = 'import'", lesson_recovery_service)
        self.assertIn("重新上传文件再解析", lesson_recovery_service)
        self.assertIn("重试生成", lesson_recovery_service)

        self.assertIn('expire_stale_assessment_plan_tasks(conn, teacher_id=int(user["id"]))', assessment_page)
        self.assertLess(
            assessment_page.index('expire_stale_assessment_plan_tasks(conn, teacher_id=int(user["id"]))'),
            assessment_page.index("ap.list_assessment_plans(conn, teacher=user)"),
        )
        self.assertIn('expire_stale_teacher_evaluation_tasks(conn, teacher_id=int(user["id"]))', teacher_page)
        self.assertLess(
            teacher_page.index('expire_stale_teacher_evaluation_tasks(conn, teacher_id=int(user["id"]))'),
            teacher_page.index("te.list_evaluations(conn, teacher=user)"),
        )
        for router_source, recovery_call, list_call in (
            (
                lesson_router,
                'expire_stale_lesson_plan_tasks(conn, teacher_id=int(user["id"]))',
                "lp.list_lesson_plans(conn, teacher=user)",
            ),
            (
                assessment_router,
                'expire_stale_assessment_plan_tasks(conn, teacher_id=int(user["id"]))',
                "ap.list_assessment_plans(conn, teacher=user)",
            ),
            (
                teacher_router,
                'expire_stale_teacher_evaluation_tasks(conn, teacher_id=int(user["id"]))',
                "te.list_evaluations(conn, teacher=user)",
            ),
        ):
            self.assertIn(recovery_call, router_source)
            self.assertLess(router_source.index(recovery_call), router_source.index(list_call))
            self.assertLess(
                router_source.index(recovery_call, router_source.index("async def get_task_status")),
                router_source.index("_load_viewable", router_source.index("async def get_task_status")),
            )

    def test_lesson_plan_preview_and_export_share_artifact_service(self):
        render_service = Path("classroom_app/services/lesson_plan_render_service.py").read_text(encoding="utf-8")
        api_router = Path("classroom_app/routers/lesson_plans.py").read_text(encoding="utf-8")
        page_router = Path("classroom_app/routers/ui_parts/lesson_plan_pages.py").read_text(encoding="utf-8")

        self.assertIn("class LessonPlanExportArtifact", render_service)
        self.assertIn("SUPPORTED_EXPORT_FORMATS = {\"docx\", \"pdf\", \"png\"}", render_service)
        self.assertIn("def export_plan_artifact(plan: dict[str, Any], *, requested_format: str = \"docx\")", render_service)
        self.assertIn("artifact = export_plan_artifact(plan, requested_format=\"docx\")", render_service)
        self.assertIn("document_render_service.render_artifact(", render_service)
        self.assertIn("def render_preview_html(plan: dict[str, Any], *, user: dict[str, Any])", render_service)
        self.assertIn("return render_preview_html(plan, user=user)", render_service)

        self.assertIn(
            "from ..services.lesson_plan_render_service import SUPPORTED_EXPORT_FORMATS, export_plan_artifact",
            api_router,
        )
        self.assertIn("if fmt not in SUPPORTED_EXPORT_FORMATS:", api_router)
        self.assertIn("artifact = export_plan_artifact(plan, requested_format=fmt)", api_router)
        self.assertNotIn("from ..services.lesson_plan_docx_service import", api_router)
        self.assertNotIn("convert_docx_to_pdf(docx_bytes", api_router)
        self.assertNotIn("convert_docx_to_png(docx_bytes", api_router)

        self.assertIn("from ...services.lesson_plan_render_service import render_preview_html", page_router)
        self.assertIn("return HTMLResponse(render_preview_html(plan, user=user))", page_router)
        self.assertNotIn("render_plan_html(plan, user=user)", page_router)

    def test_process_material_editors_offer_pdf_export_actions(self):
        assessment_template = Path("templates/assessment_plan_editor.html").read_text(encoding="utf-8")
        assessment_manage_template = Path("templates/manage/assessment_plans.html").read_text(encoding="utf-8")
        teacher_template = Path("templates/teacher_evaluation_editor.html").read_text(encoding="utf-8")
        lesson_template = Path("templates/lesson_plan_editor.html").read_text(encoding="utf-8")
        teacher_script = Path("static/js/teacher_evaluation_editor.js").read_text(encoding="utf-8")
        assessment_script = Path("static/js/assessment_plan_editor.js").read_text(encoding="utf-8")
        lesson_script = Path("static/js/lesson_plan_editor.js").read_text(encoding="utf-8")
        assessment_manage_script = Path("static/js/manage_assessment_plans.js").read_text(encoding="utf-8")
        teacher_manage_script = Path("static/js/manage_teacher_evaluations.js").read_text(encoding="utf-8")
        lesson_manage_script = Path("static/js/manage_lesson_plans.js").read_text(encoding="utf-8")
        preview_helper = Path("static/js/process_material_editor_preview.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn('id="ap-export-word"', assessment_template)
        self.assertIn('id="ap-export-pdf"', assessment_template)
        self.assertIn('id="ap-export-gate"', assessment_template)
        self.assertIn('id="ap-save-state"', assessment_template)
        self.assertIn('id="ap-open-preview"', assessment_template)
        self.assertIn("asset_url('js/assessment_plan_editor.js')", assessment_template)
        self.assertNotIn("assessment_plan_editor.js') }}?v=", assessment_template)
        self.assertIn("保存并刷新预览", assessment_template)
        self.assertIn("当前保存版预览", assessment_template)
        self.assertNotIn("实时预览", assessment_template)
        self.assertIn("from './process_material_editor_preview.js'", assessment_script)
        self.assertIn("startProcessMaterialExportDownload", assessment_script)
        self.assertIn("function assessmentExportBlocker()", assessment_script)
        self.assertIn("function setExportButtons({ busy = false } = {})", assessment_script)
        self.assertIn("function markDirty()", assessment_script)
        self.assertIn("function markClean()", assessment_script)
        self.assertIn("function restoreSaveState()", assessment_script)
        self.assertIn("saving: false", assessment_script)
        self.assertIn("function setEditorBusy(busy)", assessment_script)
        self.assertIn("state.saving = Boolean(busy);", assessment_script)
        self.assertIn("form?.classList.toggle('is-saving', state.saving);", assessment_script)
        self.assertIn("form?.querySelectorAll('input, select, textarea, button').forEach", assessment_script)
        self.assertIn("setPreviewLinkBusy(document.getElementById('ap-open-preview'), state.saving);", assessment_script)
        self.assertIn("if (state.saving) return;", assessment_script)
        self.assertIn("async function applySignatureBinding(role, signatureId)", assessment_script)
        self.assertIn("async function bindSignature(role, signatureId)", assessment_script)
        self.assertIn("setSaveState('is-saving', '更新签名中');", assessment_script)
        self.assertIn("showToast('签名已更新，预览已刷新', 'success');", assessment_script)
        self.assertIn("async function uploadSignature(role)", assessment_script)
        self.assertIn("setSaveState('is-saving', '上传签名中');", assessment_script)
        self.assertIn("await applySignatureBinding(role, signature.id);", assessment_script)
        self.assertIn("showToast('签名已上传、入库并绑定，预览已刷新。', 'success');", assessment_script)
        self.assertIn("window.addEventListener('beforeunload'", assessment_script)
        self.assertIn("button.classList.toggle('lp-btn--disabled', disabled)", assessment_script)
        self.assertIn("button.setAttribute('aria-disabled', 'true')", assessment_script)
        self.assertIn("function exportAssessmentPlan(format = 'docx')", assessment_script)
        self.assertIn("async function refreshPreview()", assessment_script)
        self.assertIn("async function openSavedPreview(event)", assessment_script)
        self.assertIn("document.getElementById('ap-refresh-preview').addEventListener('click', refreshPreview)", assessment_script)
        self.assertIn("document.getElementById('ap-open-preview').addEventListener('click', openSavedPreview)", assessment_script)
        self.assertIn("const res = await persistContent();", assessment_script)
        self.assertIn("exportAssessmentPlan('docx')", assessment_script)
        self.assertIn("exportAssessmentPlan('pdf')", assessment_script)
        self.assertIn("请调整到 100 后再导出", assessment_script)
        self.assertIn("renderTotal();", assessment_script)
        self.assertIn("`/api/assessment-plans/${state.id}/export?fmt=${normalized}`", assessment_script)
        self.assertLess(
            assessment_script.index("请调整到 100 后再导出"),
            assessment_script.index("startProcessMaterialExportDownload("),
        )
        self.assertIn("导出 Word/PDF", assessment_manage_template)
        self.assertIn("保存并刷新预览", teacher_template)
        self.assertIn("当前保存版预览", teacher_template)
        self.assertNotIn("实时预览", teacher_template)
        self.assertIn('id="te-export-word"', teacher_template)
        self.assertIn('id="te-export-pdf"', teacher_template)
        self.assertIn('id="te-export-gate"', teacher_template)
        self.assertIn('id="te-save-state"', teacher_template)
        self.assertIn('id="te-open-preview"', teacher_template)
        self.assertIn("asset_url('js/teacher_evaluation_editor.js')", teacher_template)
        self.assertNotIn("teacher_evaluation_editor.js') }}?v=", teacher_template)
        self.assertIn("from './process_material_editor_preview.js'", teacher_script)
        self.assertIn("startProcessMaterialExportDownload", teacher_script)
        self.assertIn("function setExportButtons({ busy = false } = {})", teacher_script)
        self.assertIn("function markDirty()", teacher_script)
        self.assertIn("function markClean()", teacher_script)
        self.assertIn("function restoreSaveState()", teacher_script)
        self.assertIn("saving: false", teacher_script)
        self.assertIn("function setEditorBusy(busy)", teacher_script)
        self.assertIn("state.saving = Boolean(busy);", teacher_script)
        self.assertIn("form?.classList.toggle('is-saving', state.saving);", teacher_script)
        self.assertIn("form?.querySelectorAll('input, select, textarea, button').forEach", teacher_script)
        self.assertIn("setPreviewLinkBusy(document.getElementById('te-open-preview'), state.saving);", teacher_script)
        self.assertIn("if (state.saving) return;", teacher_script)
        self.assertIn("window.addEventListener('beforeunload'", teacher_script)
        self.assertIn("评学表尚未填写完整，请先补全", teacher_script)
        self.assertIn("button.classList.toggle('lp-btn--disabled', disabled)", teacher_script)
        self.assertIn("button.setAttribute('aria-disabled', 'true')", teacher_script)
        self.assertIn("function exportEvaluation(format = 'docx')", teacher_script)
        self.assertIn("async function refreshPreview()", teacher_script)
        self.assertIn("async function openSavedPreview(event)", teacher_script)
        self.assertIn("document.getElementById('te-refresh-preview').addEventListener('click', refreshPreview)", teacher_script)
        self.assertIn("document.getElementById('te-open-preview').addEventListener('click', openSavedPreview)", teacher_script)
        self.assertIn("exportEvaluation('docx')", teacher_script)
        self.assertIn("exportEvaluation('pdf')", teacher_script)
        self.assertIn("`/api/teacher-evaluations/${state.id}/export?fmt=${normalized}`", teacher_script)
        self.assertIn("const originalText = confirmBtn?.textContent || '确认并重新编写';", teacher_script)
        self.assertIn("confirmBtn.textContent = '正在编写…';", teacher_script)
        self.assertIn("const ok = await rewriteAnalysis(prompt, { sharePrompt });", teacher_script)
        self.assertIn("if (ok) {", teacher_script)
        self.assertIn("if (state.analysisRewriting) return;", teacher_script)
        self.assertIn("if (state.saving || state.analysisRewriting) return;", teacher_script)
        self.assertIn("const closeBtn = document.getElementById('te-ai-rewrite-close');", teacher_script)
        self.assertIn("const cancelBtn = document.getElementById('te-ai-rewrite-cancel');", teacher_script)
        self.assertIn("if (closeBtn) closeBtn.disabled = state.analysisRewriting;", teacher_script)
        self.assertIn("if (cancelBtn) cancelBtn.disabled = state.analysisRewriting;", teacher_script)
        self.assertIn("closeRewriteModal();", teacher_script)
        self.assertIn("confirmBtn.textContent = originalText;", teacher_script)
        self.assertIn("return true;", teacher_script)
        self.assertIn("return false;", teacher_script)
        self.assertIn("保存并刷新预览", lesson_template)
        self.assertIn("当前保存版预览", lesson_template)
        self.assertNotIn("实时预览", lesson_template)
        self.assertIn('id="lp-export-word"', lesson_template)
        self.assertIn('id="lp-export-pdf"', lesson_template)
        self.assertIn('id="lp-export-png"', lesson_template)
        self.assertIn('id="lp-save-state"', lesson_template)
        self.assertIn('id="lp-open-preview"', lesson_template)
        self.assertIn('id="lp-import-details"', lesson_template)
        self.assertIn('"import_preview": {{ plan.import_preview | tojson }}', lesson_template)
        self.assertIn("asset_url('js/lesson_plan_editor.js')", lesson_template)
        self.assertNotIn("lesson_plan_editor.js') }}?v=", lesson_template)
        self.assertIn("from './process_material_editor_preview.js'", lesson_script)
        self.assertIn("startProcessMaterialExportDownload", lesson_script)
        self.assertIn("from './process_material_modal.js'", lesson_script)
        self.assertIn("openProcessMaterialConfirm", lesson_script)
        self.assertNotIn("confirm(", lesson_script)
        self.assertIn("title: '删除课次'", lesson_script)
        self.assertIn("function renderImportDetails()", lesson_script)
        self.assertIn("来源文件", lesson_script)
        self.assertIn("解析结果", lesson_script)
        self.assertIn("details.open = Boolean((preview.warnings || []).length)", lesson_script)
        self.assertIn("function setActionButtons({ busy = false } = {})", lesson_script)
        self.assertIn("saving: false", lesson_script)
        self.assertIn("function setEditorBusy(busy)", lesson_script)
        self.assertIn("state.saving = Boolean(busy);", lesson_script)
        self.assertIn("form?.classList.toggle('is-saving', state.saving);", lesson_script)
        self.assertIn("form?.querySelectorAll('input, select, textarea, button').forEach", lesson_script)
        self.assertIn("setPreviewLinkBusy(document.getElementById('lp-open-preview'), state.saving);", lesson_script)
        self.assertIn("if (state.saving) return;", lesson_script)
        self.assertIn("function markDirty()", lesson_script)
        self.assertIn("function markClean()", lesson_script)
        self.assertIn("function restoreSaveState()", lesson_script)
        self.assertIn("window.addEventListener('beforeunload'", lesson_script)
        self.assertIn("async function persistContent()", lesson_script)
        self.assertIn("async function saveAndRefreshPreview()", lesson_script)
        self.assertIn("async function exportLessonPlan(format = 'docx')", lesson_script)
        self.assertIn("async function openSavedPreview(event)", lesson_script)
        self.assertIn("await persistContent();", lesson_script)
        self.assertIn("document.getElementById('lp-open-preview').addEventListener('click', openSavedPreview)", lesson_script)
        self.assertIn("exportLessonPlan('docx')", lesson_script)
        self.assertIn("exportLessonPlan('pdf')", lesson_script)
        self.assertIn("exportLessonPlan('png')", lesson_script)
        self.assertIn("`/api/lesson-plans/${planId}/export?fmt=${normalized}`", lesson_script)
        lesson_export_block = re.search(
            r"async function exportLessonPlan\(format = 'docx'\)[\s\S]+?\n}\n\nfunction init",
            lesson_script,
        ).group(0)
        lesson_export_mark_clean = lesson_export_block.index("markClean();")
        lesson_export_reset = lesson_export_block.index("setEditorBusy(false);", lesson_export_mark_clean)
        lesson_export_restore = lesson_export_block.index("restoreSaveState();", lesson_export_mark_clean)
        lesson_export_download = lesson_export_block.index(
            "startProcessMaterialExportDownload("
        )
        self.assertLess(lesson_export_reset, lesson_export_download)
        self.assertLess(lesson_export_restore, lesson_export_download)
        for script_source in (lesson_script, assessment_script, teacher_script):
            open_preview_start = script_source.index("async function openSavedPreview(event)")
            preview_block = script_source[open_preview_start:]
            if "state.saving" in script_source:
                self.assertIn("if (state.saving || isPreviewLinkBusy(link))", preview_block)
            else:
                self.assertIn("if (isPreviewLinkBusy(link))", preview_block)
            self.assertIn("if (!state.dirty) return;", script_source[open_preview_start:])
            self.assertLess(
                script_source.index("persistContent();", open_preview_start),
                script_source.index("movePendingPreviewWindow(previewWindow, link.href)", open_preview_start),
            )
        self.assertIn("export function openPendingPreviewWindow", preview_helper)
        self.assertIn("window.open('about:blank', '_blank')", preview_helper)
        self.assertIn("export function setPreviewLinkBusy", preview_helper)
        self.assertIn("link.dataset.previewBusy = 'true'", preview_helper)
        self.assertIn("export function isPreviewLinkBusy", preview_helper)
        self.assertIn("export async function startProcessMaterialExportDownload", preview_helper)
        self.assertIn("import { APIError, handleAuthFailureResponse } from './api.js';", preview_helper)
        self.assertIn("await fetch(url", preview_helper)
        self.assertIn("credentials: 'same-origin'", preview_helper)
        self.assertIn("await parseDownloadError(response)", preview_helper)
        self.assertIn("throw new APIError(parsed.message, response.status, parsed.data);", preview_helper)
        self.assertIn("const blob = await response.blob();", preview_helper)
        self.assertIn("decodeDispositionFilename(response.headers.get('content-disposition'))", preview_helper)
        self.assertIn("URL.createObjectURL(blob)", preview_helper)
        self.assertIn("anchor.download = filename;", preview_helper)
        self.assertIn("showExportDownloadError(showToast, error, label);", preview_helper)
        self.assertNotIn("window.location.href = url;", preview_helper)
        self.assertIn("export async function startProcessMaterialExportDownloadFromTrigger", preview_helper)
        self.assertIn("export function bindProcessMaterialExportDownloadActions", preview_helper)
        self.assertIn("trigger.dataset.exportBusy === 'true'", preview_helper)
        self.assertIn("trigger.textContent = '准备下载…';", preview_helper)
        self.assertIn("const prefix = saved ? '已保存，' : '';", preview_helper)
        self.assertIn("event.target instanceof Element ? event.target : event.target?.parentElement", preview_helper)
        self.assertIn("target?.closest('[data-process-export-url]')", preview_helper)
        self.assertIn("showToast(`${prefix}正在准备下载${label}。`, 'success');", preview_helper)
        for manage_script in (assessment_manage_script, teacher_manage_script, lesson_manage_script):
            self.assertIn("from './process_material_editor_preview.js'", manage_script)
            self.assertIn("bindProcessMaterialExportDownloadActions(overlay, showToast, { saved: false })", manage_script)
            self.assertIn("data-process-export-url", manage_script)
            self.assertIn("data-process-export-label=\"Word\"", manage_script)
            self.assertIn("data-process-export-label=\"PDF\"", manage_script)
        self.assertIn("data-process-export-label=\"PNG\"", lesson_manage_script)
        self.assertNotIn('href="/api/assessment-plans/${id}/export', assessment_manage_script)
        self.assertNotIn('href="/api/teacher-evaluations/${id}/export', teacher_manage_script)
        self.assertNotIn('href="/api/lesson-plans/${id}/export', lesson_manage_script)
        self.assertIn(".lp-editor__export-gate", styles)
        self.assertIn(".lp-editor__save-state", styles)
        self.assertIn(".lp-editor__save-state.is-dirty", styles)
        self.assertIn(".lp-editor__form.is-saving", styles)
        self.assertIn("@media (max-width: 900px)", styles)
        self.assertIn(".lp-editor__preview { flex: 0 0 auto; display: flex;", styles)
        self.assertIn(".lp-editor__frame { display: none; }", styles)
        self.assertNotIn(".lp-editor__preview { display: none; }", styles)

    def test_teacher_evaluation_toolbar_buttons_have_direct_capture_handlers(self):
        script = Path("static/js/manage_teacher_evaluations.js").read_text(encoding="utf-8")

        self.assertIn("['[data-te-generate-open]', openGenerateModal]", script)
        self.assertIn("button.addEventListener('click'", script)
        self.assertIn("e.stopPropagation();", script)

    def test_grading_rubric_menu_does_not_auto_open_generate_modal(self):
        source = Path("classroom_app/routers/materials_parts/library.py").read_text(encoding="utf-8")
        match = re.search(
            r"manage_grading_rubrics_page[\s\S]+?initial_ai_generate=\{(?P<preset>[\s\S]+?)\}\s*,\s*\)",
            source,
        )
        self.assertIsNotNone(match)
        preset = match.group("preset")
        self.assertIn('"document_type": "grading_rubric"', preset)
        self.assertNotIn('"open": True', preset)

    def test_grading_rubric_manage_context_requires_exam_questions(self):
        weak_context = _build_manage_final_material_context(
            document_type="grading_rubric",
            prompt="",
            parent_context=None,
            attachments=[
                {
                    "title": "课程说明",
                    "content": "本课程主要讲授 Web 后端开发基础、数据库访问与项目部署。",
                }
            ],
        )
        self.assertNotIn("source_exam_paper", weak_context)

        concrete_context = _build_manage_final_material_context(
            document_type="grading_rubric",
            prompt="",
            parent_context=None,
            attachments=[
                {
                    "title": "课程考核试卷",
                    "content": "课程考核试卷\n第一题、基础环境配置（共40分）：完成账号创建并提交截图10.png。",
                }
            ],
        )
        self.assertIn("source_exam_paper", concrete_context)
        self.assertIn("截图10.png", concrete_context["source_exam_paper"]["content_markdown"])

    def test_exam_paper_manage_context_detects_assessment_plan_attachment(self):
        context = _build_manage_final_material_context(
            document_type="exam_paper",
            prompt="",
            parent_context=None,
            attachments=[
                {
                    "title": "课程考核计划表",
                    "metadata": {"document_type": "assessment_plan"},
                    "content": "课程考核计划表\n考核形式：机试\n考核技能/内容：环境部署，分值60；综合应用，分值40。",
                }
            ],
        )
        self.assertIn("source_assessment_plan", context)
        self.assertFalse(context.get("requires_assessment_plan_confirmation"))

    def test_manage_generation_candidates_expose_final_material_source_type(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE material_ai_import_records (
                    id INTEGER PRIMARY KEY,
                    package_material_id INTEGER,
                    source_material_id INTEGER,
                    parsed_material_id INTEGER,
                    document_type TEXT,
                    document_type_label TEXT,
                    parse_status TEXT,
                    updated_at TEXT
                );
                """
            )
            conn.executemany(
                """
                INSERT INTO material_ai_import_records (
                    id, package_material_id, source_material_id, parsed_material_id,
                    document_type, document_type_label, parse_status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, 10, None, None, "assessment_plan", "课程考核计划表", "completed", "2026-01-01"),
                    (2, None, None, 20, "exam_paper", "课程考核试卷", "completed", "2026-01-02"),
                    (3, 10, None, None, "grading_rubric", "评分细则", "running", "2026-01-03"),
                ],
            )
            items = _attach_ai_generation_document_source(conn, [{"id": 10}, {"id": 20}, {"id": 30}])
        finally:
            conn.close()

        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id[10]["ai_generation_document_type"], "assessment_plan")
        self.assertEqual(by_id[10]["ai_generation_document_type_label"], "课程考核计划表")
        self.assertEqual(by_id[20]["ai_generation_document_type"], "exam_paper")
        self.assertNotIn("ai_generation_document_type", by_id[30])

    def test_manage_ai_generation_guides_source_prerequisites_before_submit(self):
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        router = Path("classroom_app/routers/materials_parts/ai_import.py").read_text(encoding="utf-8")
        template = Path("templates/manage/materials.html").read_text(encoding="utf-8")

        self.assertIn("def _attach_ai_generation_document_source", router)
        self.assertIn("ai_generation_document_type", router)
        self.assertIn("function refreshAiGenerateSourceGuidance()", script)
        self.assertIn("sourceBlockReason", script)
        self.assertIn("materialLooksLikeAssessmentPlan", script)
        self.assertIn("materialLooksLikeExamPaper", script)
        self.assertIn("生成课程考核试卷前，请先关联考核计划表、课程材料或上传参考附件", script)
        self.assertIn("生成评分细则前，请先关联课程考核试卷、带题目的作业，或上传试卷文件", script)
        self.assertIn("已关联具体试卷或题目来源", script)
        self.assertIn("refreshAiGenerateSourceGuidance();", script)
        self.assertIn("refs.aiGenerateSubmitBtn.title = blocked ? blockedReason : '';", script)
        self.assertIn("refs.aiGenerateSubmitBtn.setAttribute('aria-disabled'", script)
        self.assertIn("setModalDismissDisabled(refs.aiGenerateModal, busy);", script)
        self.assertIn("AI 正在生成材料，请等待完成。", script)
        self.assertIn("refs.aiGenerateGroup,", script)
        self.assertIn("refs.aiGenerateType,", script)
        self.assertIn("renderAiGenerateModal();\n    setModalDismissDisabled(refs.aiGenerateModal, busy);", script)
        self.assertIn("const removeDisabledAttr = state.aiGenerate.busy ? ' disabled aria-disabled=\"true\"' : '';", script)
        self.assertIn("${removeDisabledAttr}>&times;</button>", script)
        self.assertIn('data-ai-generate-remove="file" data-id="${escapeHtml(entry.id)}"${removeDisabledAttr}>移除</button>', script)
        self.assertIn("const locked = state.aiGenerate.busy;", script)
        self.assertIn("selected || reachedLimit || locked ? 'disabled' : ''", script)
        self.assertIn("function addAiGenerateFiles(fileList) {\n    if (state.aiGenerate.busy) return;", script)
        self.assertIn("function removeAiGenerateAttachment(kind, idValue) {\n    if (state.aiGenerate.busy) return;", script)
        self.assertIn("function selectAiGenerateCandidate(kind, idValue) {\n    if (state.aiGenerate.busy) return;", script)
        self.assertIn("if (state.aiGenerate.busy) {\n            refs.aiGenerateFileInput.value = '';\n            return;\n        }", script)
        self.assertIn("function setAiExpandBusy(busy)", script)
        self.assertIn("state.aiExpand.busy = busy;", script)
        self.assertIn("refs.aiExpandSubmitBtn.disabled = busy;", script)
        self.assertIn("refs.aiExpandPrompt.disabled = busy;", script)
        self.assertIn("setModalDismissDisabled(document.getElementById('materials-ai-expand-modal'), busy);", script)
        self.assertIn("AI 正在续写材料，请等待任务提交完成。", script)
        self.assertIn("AI 正在续写材料，请等待当前任务提交完成。", script)
        self.assertLess(
            template.index('id="materials-ai-generate-status"'),
            template.index("materials-ai-generate-prompt-field"),
        )
        self.assertLess(
            template.index('id="materials-ai-generate-status"'),
            template.index("materials-ai-generate-source-grid"),
        )
        self.assertLess(
            script.index("refreshAiGenerateSourceGuidance();"),
            script.index("formData.append('prompt', prompt);"),
        )
        generate_submit_block = script[
            script.index("async function submitAiGenerate()"):
            script.index("function setAiRewriteStatus")
        ]
        self.assertIn("async function recordMaterialPromptBestEffort(input, prompt)", script)
        self.assertIn("/* prompt pool recording is best effort */", script)
        self.assertIn("await recordMaterialPromptBestEffort(refs.aiGeneratePrompt, prompt);", generate_submit_block)
        self.assertIn("setAiGenerateStatus(error.message || 'AI 材料生成失败', 'error');", generate_submit_block)
        self.assertNotIn("throw error;", generate_submit_block)
        delegated_submit_block = script[
            script.index("function bindEvents()"):
            script.index("const createTrigger = event.target.closest('#materials-create-menu-btn');")
        ]
        self.assertIn("const aiExpandSubmit = event.target.closest('#materials-ai-expand-submit-btn');", delegated_submit_block)
        self.assertIn("submitAiExpand().catch", delegated_submit_block)
        self.assertIn("setModalStatus(refs.aiExpandStatus, error.message || 'AI 续写失败', 'error');", delegated_submit_block)
        self.assertIn("const aiImportSubmit = event.target.closest('#materials-ai-import-submit-btn');", delegated_submit_block)
        self.assertIn("setAiImportStatus(error.message || 'AI 解析导入失败', 'error');", delegated_submit_block)
        self.assertIn("const aiGenerateSubmit = event.target.closest('#materials-ai-generate-submit-btn');", delegated_submit_block)
        self.assertIn("setAiGenerateStatus(error.message || 'AI 材料生成失败', 'error');", delegated_submit_block)
        self.assertNotIn("showToast(error.message || 'AI 材料生成失败'", delegated_submit_block)
        self.assertIn("const aiRewriteSubmit = event.target.closest('#materials-ai-rewrite-submit-btn');", delegated_submit_block)
        self.assertIn("submitAiRewrite().catch", delegated_submit_block)
        self.assertIn("if (aiRewriteSubmit.disabled) return;", delegated_submit_block)
        self.assertNotIn("state.aiGenerate.sourceBlockReason", delegated_submit_block)
        self.assertNotIn("updateAiGenerateTypeOptions();", delegated_submit_block)
        generate_group_block = script[
            script.index("refs.aiGenerateGroup?.addEventListener('change'"):
            script.index("refs.aiGenerateType?.addEventListener('change'")
        ]
        self.assertIn("state.aiGenerate.sourceBlockReason = '';", generate_group_block)
        self.assertIn("updateAiGenerateTypeOptions();", generate_group_block)
        self.assertIn("refreshAiGenerateSourceGuidance();", generate_group_block)
        self.assertNotIn("refs.aiRewriteSubmitBtn?.addEventListener('click'", script)
        self.assertNotIn("refs.aiGenerateSubmitBtn?.addEventListener('click'", script)
        self.assertNotIn("refs.aiImportSubmitBtn?.addEventListener('click'", script)
        self.assertNotIn("refs.aiExpandSubmitBtn?.addEventListener('click'", script)
        rewrite_submit_block = script[
            script.index("function submitAiRewrite()"):
            script.index("function toggleSelection")
        ]
        self.assertIn("return Promise.resolve();", rewrite_submit_block)
        self.assertIn("return apiFetch(`/api/materials/${materialId}/ai-rewrite`", rewrite_submit_block)
        self.assertIn("await recordMaterialPromptBestEffort(refs.aiRewritePrompt, prompt);", rewrite_submit_block)
        expand_submit_block = script[
            script.index("function submitAiExpand()"):
            script.index("function bindEvents()")
        ]
        self.assertIn("return Promise.resolve();", expand_submit_block)
        self.assertIn("if (state.aiExpand.busy || !state.currentFolder) return Promise.resolve();", expand_submit_block)
        self.assertIn("setAiExpandBusy(true);", expand_submit_block)
        self.assertIn("return apiFetch('/api/materials/ai-expand'", expand_submit_block)
        self.assertIn("await recordMaterialPromptBestEffort(refs.aiExpandPrompt, prompt);", expand_submit_block)
        self.assertIn("}).finally(() => {\n        setAiExpandBusy(false);", expand_submit_block)
        prompt_pool_script = Path("static/js/prompt_pool.js").read_text(encoding="utf-8")
        self.assertIn("function shouldDeferPromptPoolHide(event)", prompt_pool_script)
        self.assertIn("target.closest('button, a, input, select, textarea, [role=\"button\"]')", prompt_pool_script)
        self.assertIn("window.setTimeout(() => controller.hide(), 0);", prompt_pool_script)

    def test_material_detail_surfaces_ai_import_summary(self):
        record = {
            "id": 42,
            "document_group": "final_material",
            "document_type": "grading_rubric",
            "document_type_label": "评分细则",
            "parse_mode": "ai_generated",
            "extraction_method": "",
            "source_file_name": "grading-rubric.docx",
            "content_quality_status": "suspect",
            "updated_at": "2026-07-10T10:00:00",
            "completed_at": "2026-07-10T10:01:00",
            "metadata_json": json.dumps({"course_name": "Web 开发", "teacher_name": "张老师"}, ensure_ascii=False),
            "content_markdown": "评分细则正文",
            "parsed_payload_json": json.dumps(
                {
                    "metadata": {"course_name": "Web 开发", "teacher_name": "张老师"},
                    "warnings": ["题干缺少截图说明", "扣分项需要人工复核", "缺少命题人", "缺少审核人"],
                    "export_payload": {
                        "fields": {
                            "course_name": "Web 开发",
                            "class_name": "软件 1 班",
                            "total_score": 100,
                            "source_exam_paper_title": "期末试卷",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            "export_payload_json": json.dumps(
                {
                    "fields": {
                        "course_name": "Web 开发",
                        "class_name": "软件 1 班",
                        "total_score": 100,
                        "source_exam_paper_title": "期末试卷",
                    }
                },
                ensure_ascii=False,
            ),
            "warnings_json": json.dumps(["题干缺少截图说明"], ensure_ascii=False),
        }
        summary = _build_ai_import_detail_summary(record)
        payload = _build_ai_import_record_detail_payload(record)
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        library = Path("classroom_app/routers/materials_parts/library.py").read_text(encoding="utf-8")
        helper = Path("classroom_app/routers/materials_parts/ai_import_helpers.py").read_text(encoding="utf-8")
        island = Path("frontend/src/islands/materials-manage-page.tsx").read_text(encoding="utf-8")

        self.assertEqual(summary["parse_mode_label"], "AI 生成")
        self.assertEqual(summary["source_file_name"], "grading-rubric.docx")
        self.assertEqual(summary["content_quality_label"], "需要复核")
        self.assertEqual(summary["export_formats"], ["Word", "PDF"])
        self.assertEqual(summary["warning_count"], 4)
        self.assertTrue(summary["has_more_warnings"])
        self.assertIn("课程", {item["label"] for item in summary["field_items"]})
        self.assertEqual(payload["export_url"], "/api/materials/ai-import-records/42/export?format=docx")
        self.assertEqual(payload["export_pdf_url"], "/api/materials/ai-import-records/42/export?format=pdf")
        self.assertIn("def _build_ai_import_record_detail_payload", helper)
        self.assertIn("_build_ai_import_record_detail_payload(ai_import_record)", library)
        self.assertIn("function renderAiImportDetailSummary(detail)", script)
        self.assertIn("过程材料解析结果", script)
        self.assertIn("summary.source_file_name", script)
        self.assertIn("summary.content_quality_label", script)
        self.assertIn("renderAiImportDetailSummary(detail)", script)
        self.assertIn("ordinary-grade-wizard-20260728", island)
        self.assertIn(".materials-ai-import-summary", styles)

    def test_classroom_material_detail_surfaces_ai_import_summary(self):
        script = Path("static/js/classroom_materials.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        helper = Path("classroom_app/routers/materials_parts/ai_import_helpers.py").read_text(encoding="utf-8")

        self.assertIn("function renderClassroomAiImportSummary(material, preview = null)", script)
        self.assertIn("const aiSummaryBlock = renderClassroomAiImportSummary(material, preview);", script)
        self.assertIn("from './process_material_editor_preview.js'", script)
        self.assertIn("bindProcessMaterialExportDownloadActions(dom.detailContent, showToast, { saved: false });", script)
        self.assertIn("startProcessMaterialExportDownloadFromTrigger(dom.detailExportBtn, showToast, { saved: false });", script)
        self.assertIn("startProcessMaterialExportDownloadFromTrigger(dom.detailExportPdfBtn, showToast, { saved: false });", script)
        self.assertIn("data-process-export-url", script)
        self.assertIn("data-process-export-label", script)
        self.assertIn("dom.detailExportBtn.dataset.processExportUrl = state.detailExportUrl;", script)
        self.assertIn("dom.detailExportPdfBtn.dataset.processExportUrl = state.detailExportPdfUrl;", script)
        self.assertNotIn('href="${escapeHtml(exportUrl)}" class="btn btn-outline btn-sm"', script)
        self.assertNotIn('href="${escapeHtml(exportPdfUrl)}" class="btn btn-outline btn-sm">导出 PDF</a>', script)
        self.assertNotIn("window.location.href = state.detailExportUrl;", script)
        self.assertNotIn("window.location.href = state.detailExportPdfUrl;", script)
        self.assertLess(
            script.index("${aiSummaryBlock}"),
            script.index("${aiBlock}"),
        )
        self.assertIn("summary.source_file_name", script)
        self.assertIn("summary.content_quality_label", script)
        self.assertIn("过程材料解析结果", script)
        self.assertIn("解析警告与核对点", script)
        self.assertIn("classroom-material-ai-import-summary", styles)
        self.assertIn("AI_IMPORT_CONTENT_QUALITY_LABELS", helper)
        self.assertIn('"source_file_name": item.get("source_file_name")', helper)
        self.assertIn('"content_quality_label": _material_ai_import_quality_label', helper)

    def test_ai_import_task_cards_offer_quality_preview_and_export_actions(self):
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        helper = Path("classroom_app/routers/materials_parts/ai_import_helpers.py").read_text(encoding="utf-8")

        self.assertIn('"content_quality_label": _material_ai_import_quality_label', helper)
        self.assertIn('"export_url": export_url', helper)
        self.assertIn('"export_pdf_url": export_pdf_url', helper)
        self.assertIn('"render_preview_url": render_preview_url', helper)
        self.assertIn("from './process_material_editor_preview.js'", script)
        self.assertIn("bindProcessMaterialExportDownloadActions(document, showToast, { saved: false });", script)
        self.assertIn("task.content_quality_label", script)
        self.assertIn("task.render_preview_url", script)
        self.assertIn("task.export_url", script)
        self.assertIn("task.export_pdf_url", script)
        self.assertIn("data-process-export-url", script)
        self.assertIn("data-process-export-label", script)
        self.assertIn("const exportDownloadLabel", script)
        self.assertIn("渲染预览", script)
        self.assertIn("导出 PDF", script)
        self.assertNotIn('href="${escapeHtml(task.export_url)}"', script)
        self.assertNotIn('href="${escapeHtml(task.export_pdf_url)}"', script)
        self.assertNotIn('href="${escapeHtml(exportUrl)}" class="btn btn-outline btn-sm">${escapeHtml(exportLabel)}</a>', script)
        self.assertNotIn('href="${escapeHtml(exportPdfUrl)}" class="btn btn-outline btn-sm">导出 PDF</a>', script)
        self.assertLess(
            script.index("${renderPreviewAction}"),
            script.index("${packageAction}"),
        )

    def test_ai_import_task_dismissals_survive_refresh_without_hiding_updates(self):
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        template = Path("templates/manage/materials.html").read_text(encoding="utf-8")

        self.assertIn("userId: {{ user_info.id | tojson }}", template)
        self.assertIn('aiImportDismissStorageKey: "lanshare:materials:ai-import:dismissed:{{ user_info.id }}"', template)
        self.assertIn("const AI_IMPORT_DISMISSED_TASK_LIMIT = 80;", script)
        self.assertIn("dismissedTaskStateKeys: new Map()", script)
        self.assertIn("function getAiImportDismissStorageKey()", script)
        self.assertIn("function readAiImportDismissalEntries()", script)
        self.assertIn("window.sessionStorage?.getItem(getAiImportDismissStorageKey())", script)
        self.assertIn("function persistAiImportDismissals()", script)
        self.assertIn("window.sessionStorage?.setItem(getAiImportDismissStorageKey(), JSON.stringify(entries));", script)
        self.assertIn("function hydrateAiImportDismissals()", script)
        self.assertIn("function rememberAiImportTaskDismissal(task)", script)
        self.assertIn("function clearMismatchedAiImportDismissal(task)", script)
        self.assertIn("function isAiImportTaskDismissed(task)", script)
        self.assertIn("state.aiImport.dismissedTaskStateKeys.get(taskId) === getAiImportTaskStateKey(task)", script)
        self.assertIn("while (state.aiImport.dismissedTaskStateKeys.size > AI_IMPORT_DISMISSED_TASK_LIMIT)", script)
        self.assertIn("clearMismatchedAiImportDismissal(task);", script)
        self.assertIn("if (isAiImportTaskDismissed(task))", script)
        self.assertIn("rememberAiImportTaskDismissal(task);", script)
        self.assertIn("hydrateAiImportDismissals();\nbindEvents();", script)

    def test_material_viewer_surfaces_ai_import_summary(self):
        router = Path("classroom_app/routers/materials_parts/exports.py").read_text(encoding="utf-8")
        template = Path("templates/material_viewer.html").read_text(encoding="utf-8")
        script = Path("static/js/material_viewer.js").read_text(encoding="utf-8")
        styles = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn("_find_material_ai_import_record(", router)
        self.assertIn("completed_only=True", router)
        self.assertIn("_build_ai_import_record_detail_payload(ai_import_row)", router)
        self.assertIn('"ai_import_record": ai_import_record', router)
        self.assertIn("过程材料解析结果", template)
        self.assertIn("ai_summary.source_file_name", template)
        self.assertIn("ai_summary.content_quality_label", template)
        self.assertIn("material.ai_import_record.render_preview_url", template)
        self.assertIn("material.ai_import_record.export_url", template)
        self.assertIn("material.ai_import_record.export_pdf_url", template)
        self.assertIn("data-process-export-url", template)
        self.assertIn("data-process-export-label", template)
        self.assertNotIn('<a href="{{ material.ai_import_record.export_url }}" class="btn btn-primary">', template)
        self.assertNotIn('<a href="{{ material.ai_import_record.export_pdf_url }}" class="btn btn-outline">导出 PDF</a>', template)
        self.assertNotIn('<a href="{{ ai_record.export_url }}" class="btn btn-primary btn-sm">', template)
        self.assertNotIn('<a href="{{ ai_record.export_pdf_url }}" class="btn btn-outline btn-sm">导出 PDF</a>', template)
        self.assertIn("material.node_type == 'file'", template)
        self.assertLess(template.index("material.ai_import_record"), template.index("material.ai_parse_result"))
        self.assertIn("from './process_material_editor_preview.js'", script)
        self.assertIn("bindProcessMaterialExportDownloadActions(document, showToast, { saved: false });", script)
        self.assertIn("function buildAiImportExportActionsHtml()", script)
        self.assertIn("const aiImportActions = buildAiImportExportActionsHtml();", script)
        self.assertIn("data-process-export-url", script)
        self.assertIn("data-process-export-label", script)
        self.assertIn("const exportDownloadLabel", script)
        self.assertIn('data-process-export-label="PDF"', script)
        self.assertNotIn('href="${record.export_url}"', script)
        self.assertNotIn('href="${record.export_pdf_url}"', script)
        self.assertIn('class="materials-viewer-file-actions"', script)
        self.assertIn("if (material.node_type !== 'file')", script)
        self.assertIn("return aiImportActions", script)
        self.assertIn(".materials-viewer-ai-import", styles)
        self.assertIn(".materials-viewer-export-actions", styles)
        self.assertIn(".materials-viewer-file-actions", styles)

    def test_process_material_workflow_doc_covers_all_menu_items(self):
        doc = Path("docs/process-material-workflow-coverage.md").read_text(encoding="utf-8")
        for label in ("考核计划表", "评分细则表", "平时成绩表", "考核登分表", "教师评学表"):
            self.assertIn(label, doc)


if __name__ == "__main__":
    unittest.main()
