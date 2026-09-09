<script setup lang="ts">
/**
 * 作业/考试作答页。
 *
 * - 试卷型：按页平铺题目（单选/多选/填空/简答），checkbox 用 Web 端
 *   同款 `|||` 连接，answers_json 结构与 exam_take 完全一致，保证
 *   教师端批改/AI 批改零适配；
 * - 普通作业：单个大输入框（{answers:[{question, answer}]}）；
 * - 草稿：本地 storage 即时存 + 服务器草稿 30s 自动保存（复用
 *   /api/assignments/{id}/draft），进入时取两者较新恢复；
 * - 截止倒计时；已提交 → 结果视图（得分/批语/我的作答）。
 * - 附件随服务器草稿提交；上传、草稿同步、提交串行，未知结果先核对。
 */
import { onHide, onLoad, onShow, onUnload } from "@dcloudio/uni-app";
import { computed, reactive, ref } from "vue";

import { request, uploadFile } from "../../utils/api";
import { API_BASE } from "../../config";
import { useAuthStore } from "../../stores/auth";
import { ensurePageSession, redirectToLogin } from "../../utils/session";
import { taskDraftKey, remainingAt, TaskWriteQueue } from "../../utils/task-submission";
import { previewProtectedFile } from "../../utils/preview";
import { requestSubscribe } from "../../utils/subscribe";
import { assessmentLabel, isFormalAssessment, hasAnswerSubmission, canEnterAnswerMode, visibleTaskScore, type AssessmentClassification, type SubmissionPresence } from "../../utils/assessment";

interface Question {
  id: string;
  type: string;
  text: string;
  options?: string[];
  placeholder?: string;
}

interface DraftFile {
  id: number;
  question_id: string;
  kind: string;
  file_name: string;
  relative_path: string;
  mime_type: string;
  file_size: number | null;
  is_image: boolean;
}

interface DraftResponse {
  exists?: boolean;
  answers_json?: string;
  server_updated_at?: string;
  files_by_question?: Record<string, DraftFile[]>;
}

interface DetailData {
  assignment: AssessmentClassification & {
    id: number | string;
    title: string;
    requirements_md: string;
    is_exam: boolean;
    due_at: string | null;
    remaining_seconds: number | null;
    availability_mode_label: string;
    is_accepting_submissions: boolean;
    late_policy_label: string;
    can_submit?: boolean;
    submission_version: string;
    draft_revision: string;
    answer_remaining_seconds: number | null;
    server_now_ms: number;
  };
  paper: { title: string; description: string; pages?: Array<{ name?: string; questions?: Question[] }> } | null;
  submission: (SubmissionPresence & {
    status: string;
    score: number | null;
    feedback_md: string;
    submitted_at: string;
    version: string;
    returned_at?: string;
    returned_reason?: string;
    resubmission_due_at?: string;
    answers: Array<{ question_id?: string; question?: string; answer?: string }>;
    files: Array<{
      id: number;
      file_name: string;
      mime_type: string;
      is_image: boolean;
    }>;
  }) | null;
  group: {
    is_group: boolean;
    in_group: boolean;
    group_name: string;
    revealed: boolean;
    final_score: number | null;
    pending: boolean;
    peers: Array<{ student_id: number; name: string; avatar_url?: string }>;
    my_ratings: Record<string, number>;
  } | null;
}

const CHECKBOX_SEP = "|||";
const DRAFT_INTERVAL_MS = 30_000;
const PLAIN_QUESTION_LABEL = "作答";
/** 普通作业附件的固定挂载位（服务器按 question_id 归组） */
const PLAIN_FILE_QID = "attachment";
const plainFileQuestion: Question = { id: PLAIN_FILE_QID, type: "attachment", text: "附件" };

const assignmentId = ref("");
const detail = ref<DetailData | null>(null);
const loading = ref(true);
const failed = ref(false);
const errorMessage = ref("");
const answers = reactive<Record<string, string>>({});
const questionFiles = reactive<Record<string, DraftFile[]>>({});
/** 本会话刚上传文件的本地临时路径（服务器下载需鉴权头，image 组件带不了） */
const localPreview = reactive<Record<string, string>>({});
const uploadingQid = ref("");
const plainAnswer = ref("");
const submitting = ref(false);
const remainingSeconds = ref<number | null>(null);
const draftSavedAt = ref("");
const draftStatus = ref("");
const checkingResult = ref(false);
const pendingSubmissionVersion = ref<string | null>(null);
const pendingDraftCheck = ref(false);
const selectingFiles = ref(false);
const auth = useAuthStore();
const writes = new TaskWriteQueue();
let pageOwner = "";
let hydratedKey = "";
let detailRequest: Promise<void> | null = null;
let countdownReceivedAt = 0;
let countdownInitial: number | null = null;
let serverClockOffset = 0;
let pageVisible = false;
let disposed = false;
const submitConfirmed = ref(false);
let pendingAnswerRestore = false;
const editedQuestions = new Set<string>();
let plainEdited = false;

let draftTimer: ReturnType<typeof setInterval> | null = null;
let countdownTimer: ReturnType<typeof setInterval> | null = null;
let startedAt = "";
let lastDraftSignature = "";

const localDraftKey = computed(() => taskDraftKey(API_BASE, auth.user, assignmentId.value,
  detail.value?.assignment.draft_revision || ""));
const currentOwner = () => auth.user ? `${API_BASE}:${auth.user.role}:${auth.user.id}` : "";
const ownsPage = () => !disposed && Boolean(pageOwner) && currentOwner() === pageOwner;
const controlsLocked = computed(() => submitting.value || checkingResult.value || pendingSubmissionVersion.value !== null);
const canChangeAttachments = () => ownsPage() && isAnswerMode.value && !controlsLocked.value && !uploadingQid.value && !selectingFiles.value;

const allQuestions = computed<Question[]>(() => {
  const pages = detail.value?.paper?.pages ?? [];
  return pages.flatMap((page) => page.questions ?? []);
});

const isAnswerMode = computed(
  () => Boolean(!submitConfirmed.value && detail.value && canEnterAnswerMode(detail.value.submission, detail.value.assignment.is_accepting_submissions, detail.value.assignment.can_submit)),
);

const answeredCount = computed(
  () => allQuestions.value.filter((q) => (answers[q.id] || "").trim() || questionFiles[q.id]?.length).length,
);

/** 组员互评（20 分制）本地评分表 */
const peerRatings = reactive<Record<string, number>>({});
const peerSaving = ref(false);

const showPeerEval = computed(
  () =>
    Boolean(
      hasAnswerSubmission(detail.value?.submission) &&
        detail.value?.group?.in_group &&
        (detail.value?.group?.peers?.length ?? 0) > 0,
    ),
);

/** 小组作业的成绩展示：揭晓前隐藏个人分，揭晓后显示综合表现分 */
const scoreDisplay = computed(() => {
  const group = detail.value?.group;
  const submission = detail.value?.submission;
  if (!submission) return { value: "—", label: "待批改", note: "" };
  if (submission.score_visible === false) return { value: "—", label: group?.pending ? "待揭晓" : "暂无可见成绩", note: "" };
  if (submission.is_absence_score && visibleTaskScore(submission) !== null) {
    return { value: String(visibleTaskScore(submission)), label: "缺交记分", note: "尚未提交答卷，能否补交以当前开放状态为准" };
  }
  if (group?.is_group) {
    if (group.revealed && group.final_score !== null && group.final_score !== undefined) {
      return { value: String(group.final_score), label: "综合表现分", note: "作业分×0.8 + 组员互评均分" };
    }
    if (group.pending) {
      return { value: "…", label: "待揭晓", note: "已批改，等待全组完成后统一揭晓" };
    }
    return { value: "—", label: "待批改", note: `小组：${group.group_name || "未分组"}` };
  }
  if (submission.score !== null && submission.score !== undefined) {
    return { value: String(submission.score), label: "得分", note: submission.grade_display_state === "regrading" ? "重批中，保留原有效成绩" : "" };
  }
  return { value: "—", label: "待批改", note: "" };
});

function initPeerRatings(): void {
  const group = detail.value?.group;
  if (!group?.in_group) return;
  for (const peer of group.peers) {
    const key = String(peer.student_id);
    const existing = group.my_ratings?.[key];
    peerRatings[key] = typeof existing === "number" ? existing : 15;
  }
}

function onPeerSlider(peerId: number, event: { detail: { value: number } }): void {
  peerRatings[String(peerId)] = event.detail.value;
}

async function submitPeerRatings(): Promise<void> {
  if (peerSaving.value || !detail.value?.group) return;
  peerSaving.value = true;
  try {
    await request({
      path: `/api/assignments/${assignmentId.value}/peer-eval`,
      method: "POST",
      data: {
        ratings: detail.value.group.peers.map((peer) => ({
          reviewee_student_id: peer.student_id,
          points: peerRatings[String(peer.student_id)] ?? 15,
        })),
      },
    });
    uni.showToast({ title: "互评已提交", icon: "success" });
  } catch (error: unknown) {
    uni.showToast({
      title: error instanceof Error ? error.message : "提交失败",
      icon: "none",
    });
  } finally {
    peerSaving.value = false;
  }
}

const countdownLabel = computed(() => {
  const total = remainingSeconds.value;
  if (total === null || total === undefined) return "";
  if (total <= 0) return "已截止";
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}时${m}分`;
  if (m > 0) return `${m}分${s}秒`;
  return `${s}秒`;
});

function isChoice(q: Question): boolean {
  return q.type === "radio" || q.type === "checkbox";
}

function isAttachmentQuestion(q: Question): boolean {
  return !["radio", "checkbox", "text", "textarea"].includes(q.type);
}

function checkboxSelected(q: Question, option: string): boolean {
  return (answers[q.id] || "").split(CHECKBOX_SEP).includes(option);
}

function tapOption(q: Question, option: string): void {
  if (!isAnswerMode.value || controlsLocked.value) return;
  if (q.type === "radio") {
    answers[q.id] = answers[q.id] === option ? "" : option;
  } else {
    const current = (answers[q.id] || "").split(CHECKBOX_SEP).filter(Boolean);
    const next = current.includes(option)
      ? current.filter((item) => item !== option)
      : [...current, option];
    answers[q.id] = next.join(CHECKBOX_SEP);
  }
  editedQuestions.add(String(q.id));
  saveLocalDraft();
}

function buildAnswersJson(): string {
  if (detail.value?.paper) {
    return JSON.stringify({
      answers: allQuestions.value.map((q) => ({
        question_id: q.id,
        question: q.text,
        type: q.type || "",
        answer: answers[q.id] || "",
        attachments: (questionFiles[q.id] || []).map((file) => ({
          kind: file.kind || (file.is_image ? "image" : "file"),
          file_name: file.file_name,
          relative_path: file.relative_path,
          mime_type: file.mime_type,
          file_size: file.file_size || 0,
          question_id: q.id,
        })),
      })),
    });
  }
  return JSON.stringify({
    answers: [{ question: PLAIN_QUESTION_LABEL, answer: plainAnswer.value }],
  });
}

// ---------- 草稿 ----------

function saveLocalDraft(): void {
  if (!ownsPage() || !localDraftKey.value || !isAnswerMode.value || submitConfirmed.value) return;
  try {
    uni.setStorageSync(localDraftKey.value, {
      answers: { ...answers },
      plain: plainAnswer.value,
      saved_at: new Date(Date.now() + serverClockOffset).toISOString(),
      pending_submission_version: pendingSubmissionVersion.value,
      pending_draft_check: pendingDraftCheck.value,
      started_at: startedAt,
      pending_answer_restore: pendingAnswerRestore,
      edited_questions: [...editedQuestions],
      plain_edited: plainEdited,
    });
    if (!draftSavedAt.value) draftStatus.value = "作答已保存在本机";
  } catch {
    draftStatus.value = "本机保存失败，请保持页面打开并同步草稿";
  }
}

function restoreFromAnswersList(
  list: Array<{ question_id?: string; question?: string; answer?: string }>,
): void {
  for (const item of list) {
    const qid = String(item.question_id || "");
    if (qid) {
      answers[qid] = String(item.answer || "");
    } else if (item.question === PLAIN_QUESTION_LABEL || list.length === 1) {
      plainAnswer.value = String(item.answer || "");
    }
  }
}

async function restoreDrafts(): Promise<void> {
  let localSavedAt = "";
  try {
    const local = uni.getStorageSync(localDraftKey.value) as {
      answers?: Record<string, string>;
      plain?: string;
      saved_at?: string;
      pending_submission_version?: string | null;
      pending_draft_check?: boolean;
      started_at?: string;
      pending_answer_restore?: boolean;
      edited_questions?: string[];
      plain_edited?: boolean;
    };
    if (local && typeof local === "object") {
      Object.assign(answers, local.answers || {});
      plainAnswer.value = local.plain || "";
      localSavedAt = local.saved_at || "";
      pendingSubmissionVersion.value = local.pending_submission_version ?? null;
      pendingDraftCheck.value = Boolean(local.pending_draft_check);
      startedAt = local.started_at || startedAt;
      pendingAnswerRestore = Boolean(local.pending_answer_restore);
      for (const qid of local.edited_questions || Object.keys(local.answers || {})) editedQuestions.add(qid);
      plainEdited = local.plain_edited ?? Boolean(local.plain);
    }
  } catch {
    /* ignore */
  }
  try {
    const draft = await request<DraftResponse>({
      path: `/api/assignments/${assignmentId.value}/draft`,
    });
    if (!ownsPage()) return;
    pendingDraftCheck.value = false;
    if (pendingAnswerRestore) restoreRecoveredAnswers(draft);
    if (draft?.exists) {
      applyQuestionFiles(draft.files_by_question);
      if (draft.answers_json) {
        const serverTime = Date.parse(draft.server_updated_at || "") || 0;
        const localTime = Date.parse(localSavedAt) || 0;
        if (serverTime >= localTime) {
          const parsed = JSON.parse(draft.answers_json) as { answers?: [] };
          restoreFromAnswersList(parsed.answers || []);
        }
      }
    }
  } catch {
    draftStatus.value = "已恢复本机内容，服务器草稿待网络恢复后核对";
    pendingDraftCheck.value = true;
    pendingAnswerRestore = true;
  }
}

function restoreRecoveredAnswers(draft: DraftResponse): void {
  if (draft.answers_json) {
    const parsed = JSON.parse(draft.answers_json) as { answers?: Array<{ question_id?: string; question?: string; answer?: string }> };
    const recovered = (parsed.answers || []).filter((item) => item.question_id ? !editedQuestions.has(String(item.question_id)) : !plainEdited);
    restoreFromAnswersList(recovered);
  }
  pendingAnswerRestore = false;
}

function applyQuestionFiles(filesByQuestion?: Record<string, DraftFile[]>): void {
  if (!filesByQuestion) return;
  for (const key of Object.keys(questionFiles)) {
    delete questionFiles[key];
  }
  for (const [qid, files] of Object.entries(filesByQuestion)) {
    questionFiles[qid] = files;
  }
}

// ---------- 附件上传（拍照/相册 → 服务器草稿，提交时 use_server_draft 合并） ----------

function chooseImages(count: number): Promise<Array<{ path: string; size: number }>> {
  return new Promise((resolve) => {
    uni.chooseImage({
      count,
      sizeType: ["compressed"],
      sourceType: ["album", "camera"],
      success: (res) => {
        const sizes = (res.tempFiles as Array<{ size: number }>) || [];
        resolve(
          (res.tempFilePaths as string[]).map((path, index) => ({
            path,
            size: sizes[index]?.size || 0,
          })),
        );
      },
      fail: () => resolve([]),
    });
  });
}

/** 从聊天记录选文件（md/txt/代码/文档等，微信小程序唯一的通用文件入口） */
function chooseChatFiles(count: number): Promise<Array<{ path: string; size: number; name: string }>> {
  return new Promise((resolve) => {
    uni.chooseMessageFile({
      count,
      type: "file",
      success: (res) => {
        resolve(
          (res.tempFiles || []).map((file) => ({
            path: file.path,
            size: file.size || 0,
            name: file.name || "file.bin",
          })),
        );
      },
      fail: () => resolve([]),
    });
  });
}

function guessMime(name: string): string {
  const ext = (name.split(".").pop() || "").toLowerCase();
  const map: Record<string, string> = {
    png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
    pdf: "application/pdf", txt: "text/plain", md: "text/markdown",
    doc: "application/msword",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    zip: "application/zip",
  };
  return map[ext] || "application/octet-stream";
}

async function verifyDraftFiles(): Promise<boolean> {
  try {
    const draft = await request<DraftResponse>({ path: `/api/assignments/${assignmentId.value}/draft` });
    if (!ownsPage()) return false;
    applyQuestionFiles(draft.files_by_question || {});
    if (pendingAnswerRestore) restoreRecoveredAnswers(draft);
    pendingDraftCheck.value = false;
    saveLocalDraft();
    return true;
  } catch {
    pendingDraftCheck.value = true;
    draftStatus.value = "附件状态待核对，网络恢复后再提交";
    saveLocalDraft();
    return false;
  }
}

async function uploadEntries(
  q: Question,
  entries: Array<{ path: string; size: number; name: string; kind: "image" | "file" }>,
): Promise<void> {
  if (!canChangeAttachments()) return;
  const version = detail.value!.assignment.submission_version;
  uploadingQid.value = q.id;
  let completed = 0;
  try {
    await writes.idle();
    if (!ownsPage() || controlsLocked.value || !isAnswerMode.value) return;
    if (pendingDraftCheck.value && !await verifyDraftFiles()) throw new Error("请先核对上次上传结果");
    await writes.run(async () => {
      for (const item of entries) {
        if (!ownsPage()) return;
        const relativePath = detail.value?.paper ? `exam_question_files/${q.id}/${item.name}` : item.name;
        const manifest = JSON.stringify([{
          relative_path: relativePath, file_name: item.name, question_id: q.id,
          kind: item.kind, mime_type: guessMime(item.name), file_size: item.size,
        }]);
        // Persist uncertainty before sending, so leaving the page cannot hide it.
        pendingDraftCheck.value = true;
        saveLocalDraft();
        const payload = buildAnswersJson();
        const draft = await uploadFile<DraftResponse>({
          path: `/api/assignments/${assignmentId.value}/draft`, filePath: item.path,
          formData: { answers_json: payload, current_page: "0", manifest,
            expected_submission_version: version },
        });
        if (!ownsPage()) return;
        applyQuestionFiles(draft.files_by_question || {});
        pendingDraftCheck.value = false;
        lastDraftSignature = payload;
        completed += 1;
        const uploaded = (questionFiles[q.id] || []).find((file) => file.relative_path === relativePath);
        if (uploaded && item.kind === "image") localPreview[uploaded.relative_path] = item.path;
      }
    });
    if (!ownsPage()) return;
    draftSavedAt.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    draftStatus.value = `已上传 ${completed} 个附件`;
    uni.showToast({ title: "附件已保存到草稿", icon: "success" });
  } catch (error: unknown) {
    if (!ownsPage()) return;
    const verified = await verifyDraftFiles();
    if (verified) completed = entries.filter((entry) => (questionFiles[q.id] || []).some((file) =>
      file.relative_path === (detail.value?.paper ? `exam_question_files/${q.id}/${entry.name}` : entry.name))).length;
    uni.showModal({
      title: completed ? "部分附件已上传" : "上传未完成",
      content: `已确认上传 ${completed}/${entries.length} 个。${verified ? "请核对当前附件列表，再补传缺少的文件。" : "上传结果暂时无法确认，请恢复网络后核对。"} ${error instanceof Error ? error.message : "网络异常"}`,
      showCancel: false,
    });
  } finally {
    uploadingQid.value = "";
    if (ownsPage()) saveLocalDraft();
  }
}

async function addPhotos(q: Question): Promise<void> {
  if (!canChangeAttachments()) return;
  selectingFiles.value = true;
  const picked = await chooseImages(3);
  selectingFiles.value = false;
  if (!picked.length || !canChangeAttachments()) return;
  await uploadEntries(q, picked.map((item, index) => ({
    ...item, name: `mp_${q.id}_${Date.now()}_${index}.${item.path.split(".").pop() || "jpg"}`, kind: "image" as const,
  })));
}

async function addChatFiles(q: Question): Promise<void> {
  if (!canChangeAttachments()) return;
  selectingFiles.value = true;
  const picked = await chooseChatFiles(3);
  selectingFiles.value = false;
  if (!picked.length || !canChangeAttachments()) return;
  await uploadEntries(q, picked.map((item) => ({ ...item, kind: "file" as const })));
}

async function clearFiles(q: Question): Promise<void> {
  if (!canChangeAttachments() || !(questionFiles[q.id] || []).length) return;
  const version = detail.value!.assignment.submission_version;
  selectingFiles.value = true;
  const confirmed = await new Promise<boolean>((resolve) => {
    uni.showModal({ title: "清空附件", content: "删除本题已上传的全部附件？",
      success: (res) => resolve(Boolean(res.confirm)), fail: () => resolve(false) });
  });
  selectingFiles.value = false;
  if (!confirmed || !canChangeAttachments()) return;
  uploadingQid.value = q.id;
  try {
    await writes.idle();
    if (!ownsPage()) return;
    pendingDraftCheck.value = true;
    saveLocalDraft();
    const draft = await writes.run(() => request<DraftResponse>({
      path: `/api/assignments/${assignmentId.value}/draft`, method: "POST", form: true,
      data: { answers_json: buildAnswersJson(), current_page: 0, replace_question_ids: JSON.stringify([q.id]),
        expected_submission_version: version },
    }));
    if (!ownsPage()) return;
    applyQuestionFiles(draft.files_by_question || {});
    pendingDraftCheck.value = false;
  } catch (error: unknown) {
    if (!ownsPage()) return;
    await verifyDraftFiles();
    uni.showToast({ title: error instanceof Error ? error.message : "操作未完成，请核对附件", icon: "none" });
  } finally {
    uploadingQid.value = "";
    if (ownsPage()) saveLocalDraft();
  }
}

async function saveServerDraft(): Promise<void> {
  if (!ownsPage() || !isAnswerMode.value || controlsLocked.value || uploadingQid.value || writes.busy || pendingDraftCheck.value) return;
  const version = detail.value!.assignment.submission_version;
  const payload = buildAnswersJson();
  if (payload === lastDraftSignature) return;
  try {
    await writes.run(() => request({
      path: `/api/assignments/${assignmentId.value}/draft`, method: "POST", form: true,
      data: { answers_json: payload, current_page: 0,
        expected_submission_version: version },
    }));
    if (!ownsPage()) return;
    lastDraftSignature = payload;
    draftSavedAt.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    draftStatus.value = "";
  } catch {
    if (ownsPage()) draftStatus.value = "服务器同步失败，作答保留在本机";
  }
}

// ---------- 倒计时 ----------

function startCountdown(): void {
  if (countdownTimer) clearInterval(countdownTimer);
  if (!pageVisible || remainingSeconds.value === null || remainingSeconds.value <= 0) return;
  countdownTimer = setInterval(() => {
    remainingSeconds.value = remainingAt(countdownInitial, countdownReceivedAt, Date.now());
    if (remainingSeconds.value === 0 && countdownTimer) {
      clearInterval(countdownTimer);
      countdownTimer = null;
      saveLocalDraft();
      // The server decides whether late submission or a personal reopen is valid.
      draftStatus.value = "正在核对最新提交时间";
      void loadDetail();
    }
  }, 1000);
}

// ---------- 加载与提交 ----------

function clearAnswerState(): void {
  for (const state of [answers, questionFiles, localPreview, peerRatings]) {
    for (const key of Object.keys(state)) delete state[key];
  }
  plainAnswer.value = "";
  pendingSubmissionVersion.value = null;
  pendingDraftCheck.value = false;
  draftSavedAt.value = "";
  draftStatus.value = "";
  lastDraftSignature = "";
  submitConfirmed.value = false;
  pendingAnswerRestore = false;
  editedQuestions.clear();
  plainEdited = false;
}

function applyClock(data: DetailData): void {
  countdownInitial = data.assignment.answer_remaining_seconds ?? data.assignment.remaining_seconds;
  countdownReceivedAt = Date.now();
  serverClockOffset = Number.isFinite(data.assignment.server_now_ms) ? data.assignment.server_now_ms - countdownReceivedAt : 0;
  remainingSeconds.value = countdownInitial;
  startCountdown();
}

async function loadDetail(): Promise<void> {
  if (detailRequest) return detailRequest;
  detailRequest = (async () => {
    if (!await ensurePageSession("student") || disposed) return;
    const owner = currentOwner();
    if (pageOwner !== owner) {
      clearAnswerState();
      detail.value = null;
      hydratedKey = "";
      pageOwner = owner;
    }
    if (!detail.value) loading.value = true;
    failed.value = false;
    try {
      const data = await request<DetailData>({ path: `/api/mp/tasks/assignment/${assignmentId.value}` });
      if (!ownsPage()) return;
      const previousKey = localDraftKey.value;
      detail.value = data;
      applyClock(data);
      if (hydratedKey !== localDraftKey.value) {
        clearAnswerState();
        startedAt = new Date(Date.now() + serverClockOffset).toISOString();
        if (hasAnswerSubmission(data.submission) && data.submission) restoreFromAnswersList(data.submission.answers || []);
        if (isAnswerMode.value) await restoreDrafts();
        hydratedKey = localDraftKey.value;
      }
      initPeerRatings();
      if (!isAnswerMode.value && hasAnswerSubmission(data.submission) && !data.submission?.is_returned) {
        submitConfirmed.value = true;
        pendingSubmissionVersion.value = null;
        try { if (previousKey) uni.removeStorageSync(previousKey); } catch { /* ignore */ }
      }
    } catch (error: unknown) {
      if (!ownsPage()) return;
      errorMessage.value = error instanceof Error ? error.message : "加载失败";
      if (!detail.value) failed.value = true;
      else draftStatus.value = "最新提交状态暂时无法核对，请恢复网络后重试";
      if ((error as { statusCode?: number }).statusCode === 401) redirectToLogin();
    } finally { loading.value = false; }
  })();
  try { await detailRequest; } finally { detailRequest = null; }
}

async function reconcileSubmission(): Promise<boolean> {
  if (pendingSubmissionVersion.value === null) return true;
  checkingResult.value = true;
  try {
    const data = await request<DetailData>({ path: `/api/mp/tasks/assignment/${assignmentId.value}` });
    if (!ownsPage()) return false;
    if (hasAnswerSubmission(data.submission) && !data.submission?.is_returned) {
      const key = localDraftKey.value;
      detail.value = data;
      submitConfirmed.value = true;
      pendingSubmissionVersion.value = null;
      try { uni.removeStorageSync(key); } catch { /* ignore */ }
      initPeerRatings();
      uni.showToast({ title: "已确认提交成功", icon: "success" });
      return false;
    }
    if (data.assignment.submission_version !== pendingSubmissionVersion.value) {
      // A teacher has changed the reopen window; hydrate the new round first.
      hydratedKey = "";
      pendingSubmissionVersion.value = null;
      saveLocalDraft();
      await loadDetail();
      uni.showToast({ title: "任务状态已变化，请核对后提交", icon: "none" });
      return false;
    }
    detail.value = data;
    applyClock(data);
    pendingSubmissionVersion.value = null;
    saveLocalDraft();
    // An older request may still be running: a retry carries this same expected
    // version and the server's transactional guard admits only one writer.
    draftStatus.value = "服务器尚未确认提交，可核对内容后再次提交";
    return true;
  } catch {
    draftStatus.value = "提交结果待确认，请恢复网络后点击核对提交结果";
    saveLocalDraft();
    return false;
  } finally { checkingResult.value = false; }
}

async function submit(): Promise<void> {
  if (submitting.value || checkingResult.value || !detail.value || !ownsPage()) return;
  if (uploadingQid.value || selectingFiles.value) {
    uni.showToast({ title: "请等待附件上传完成", icon: "none" });
    return;
  }
  if (pendingSubmissionVersion.value !== null) {
    await reconcileSubmission();
    return;
  }
  if (!isAnswerMode.value) return;
  submitting.value = true;
  let attempted = false;
  try {
    await writes.idle();
    if (!ownsPage()) return;
    if (pendingDraftCheck.value && !await verifyDraftFiles()) return;
    const hasAnyFiles = Object.values(questionFiles).some((files) => files.length > 0);
    const hasContent = detail.value.paper ? answeredCount.value > 0 || hasAnyFiles : Boolean(plainAnswer.value.trim()) || hasAnyFiles;
    if (!hasContent) { uni.showToast({ title: "还没有填写任何作答内容", icon: "none" }); return; }
    const unanswered = detail.value.paper ? allQuestions.value.length - answeredCount.value : 0;
    const confirmed = await new Promise<boolean>((resolve) => {
      uni.showModal({ title: "确认提交", content: unanswered > 0 ? `还有 ${unanswered} 题未作答，确定提交吗？` : "提交后将不能再修改，确定提交吗？",
        success: (res) => resolve(Boolean(res.confirm)), fail: () => resolve(false) });
    });
    if (!confirmed || !ownsPage()) return;
    requestSubscribe(["graded", "deadline", "nudge"]);
    const version = detail.value.assignment.submission_version;
    const key = localDraftKey.value;
    pendingSubmissionVersion.value = version;
    saveLocalDraft();
    attempted = true;
    const result = await writes.run(() => request<{ dropped_file_count?: number; dropped_file_message?: string }>({
      path: `/api/assignments/${assignmentId.value}/submit`, method: "POST", form: true,
      data: { answers_json: buildAnswersJson(), started_at: startedAt, use_server_draft: "true", expected_submission_version: version },
    }));
    if (!ownsPage()) return;
    submitConfirmed.value = true;
    pendingSubmissionVersion.value = null;
    try { uni.removeStorageSync(key); } catch { /* ignore */ }
    if (result.dropped_file_count) uni.showModal({ title: "提交附件需要核对", content: result.dropped_file_message || "部分附件未接收，请核对提交结果并联系教师。", showCancel: false });
    else uni.showToast({ title: "提交成功", icon: "success" });
    await loadDetail();
  } catch (error: unknown) {
    if (!ownsPage()) return;
    // Even a server error can follow a commit; always check before a new POST.
    if (attempted) await reconcileSubmission();
    if (!submitConfirmed.value) uni.showModal({
      title: pendingSubmissionVersion.value !== null ? "提交结果待确认" : "本次提交未确认",
      content: pendingSubmissionVersion.value !== null ? "网络异常，作答已保留。恢复网络后点击核对提交结果。" : (error instanceof Error ? error.message : "请核对内容后重试。"),
      showCancel: false,
    });
  } finally { submitting.value = false; }
}

function previewDraftFile(file: DraftFile): void {
  void previewProtectedFile({
    path: `/api/assignments/${assignmentId.value}/draft-files/${file.id}`,
    fileName: file.file_name,
    mimeType: file.mime_type,
    localPath: localPreview[file.relative_path],
  });
}

function previewSubmissionFile(file: { id: number; file_name: string; mime_type: string }): void {
  void previewProtectedFile({
    path: `/submissions/download/${file.id}`,
    fileName: file.file_name,
    mimeType: file.mime_type,
  });
}

function onTextInput(qid: string, event: { detail: { value: string } }): void {
  if (!ownsPage() || controlsLocked.value) return;
  answers[qid] = event.detail.value;
  editedQuestions.add(String(qid));
  saveLocalDraft();
}

function onPlainInput(event: { detail: { value: string } }): void {
  if (!ownsPage() || controlsLocked.value) return;
  plainAnswer.value = event.detail.value;
  plainEdited = true;
  saveLocalDraft();
}

onLoad((query) => {
  assignmentId.value = String((query as Record<string, string>)?.id || "");
  if (!assignmentId.value) {
    failed.value = true;
    errorMessage.value = "缺少任务参数";
    loading.value = false;
    return;
  }
  void loadDetail();
});

onShow(() => {
  pageVisible = true;
  if (!assignmentId.value) return;
  void loadDetail();
  if (draftTimer) clearInterval(draftTimer);
  draftTimer = setInterval(() => void saveServerDraft(), DRAFT_INTERVAL_MS);
});

onHide(() => {
  pageVisible = false;
  if (draftTimer) clearInterval(draftTimer);
  if (countdownTimer) clearInterval(countdownTimer);
  saveLocalDraft();
  void saveServerDraft();
});

onUnload(() => {
  if (draftTimer) clearInterval(draftTimer);
  if (countdownTimer) clearInterval(countdownTimer);
  saveLocalDraft();
  disposed = true;
});
</script>

<template>
  <view class="detail">
    <view v-if="loading" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="loadDetail">
      <text>{{ errorMessage }}，点击重试</text>
    </view>

    <template v-else-if="detail">
      <!-- 头部信息 -->
      <view class="head-card glass-card">
        <view class="head-card__top">
          <text class="head-card__badge" :class="{ 'head-card__badge--exam': isFormalAssessment(detail.assignment) }">
            {{ assessmentLabel(detail.assignment) }}
          </text>
          <text v-if="countdownLabel && isAnswerMode" class="head-card__countdown">
            ⏱ {{ countdownLabel }}
          </text>
        </view>
        <text class="head-card__title">{{ detail.assignment.title }}</text>
        <text v-if="detail.assignment.requirements_md" class="head-card__req">
          {{ detail.assignment.requirements_md }}
        </text>
      </view>

      <view v-if="detail.submission?.is_returned" class="return-card glass-card">
        <text class="section-title">{{ isAnswerMode ? "教师已退回，请修改后重新提交" : "本次重交窗口已关闭" }}</text>
        <text v-if="detail.submission.returned_reason">退回原因：{{ detail.submission.returned_reason }}</text>
        <text v-if="detail.submission.resubmission_due_at">重交截止：{{ detail.submission.resubmission_due_at }}</text>
        <text v-if="detail.submission.resubmission_state === 'invalid'">重交时间尚未配置完整，请联系教师确认。</text>
        <text v-if="detail.submission.files?.length">上次提交的附件仅供查看；本次需提交的附件请重新上传到下方作答区。</text>
      </view>

      <!-- 结果视图 -->
      <template v-if="detail.submission">
        <view class="result-card glass-card">
          <view class="result-card__score-row">
            <text class="result-card__score">{{ scoreDisplay.value }}</text>
            <text class="result-card__score-label">{{ scoreDisplay.label }}</text>
          </view>
          <text v-if="scoreDisplay.note" class="result-card__meta">{{ scoreDisplay.note }}</text>
          <text v-if="hasAnswerSubmission(detail.submission) && detail.submission.submitted_at" class="result-card__meta">提交于 {{ detail.submission.submitted_at }}</text>
          <view v-if="detail.submission.feedback_md" class="result-card__feedback">
            <text class="result-card__feedback-title">教师批语</text>
            <text class="result-card__feedback-text">{{ detail.submission.feedback_md }}</text>
          </view>
        </view>

        <!-- 组员互评（20 分制，仅教师可见结果） -->
        <view v-if="showPeerEval" class="peer-card glass-card">
          <text class="section-title">组员互评 · {{ detail.group?.group_name }}</text>
          <text class="peer-card__hint">给每位组员的本次贡献打分（0-20 分），仅教师可见，可随时修改。</text>
          <view v-for="peer in detail.group?.peers" :key="peer.student_id" class="peer-row">
            <text class="peer-row__name">{{ peer.name }}</text>
            <slider
              class="peer-row__slider"
              :value="peerRatings[String(peer.student_id)] ?? 15"
              :min="0"
              :max="20"
              :step="1"
              show-value
              activeColor="#4a7dff"
              @change="onPeerSlider(peer.student_id, $event as never)"
            />
          </view>
          <button class="peer-card__submit glass-btn-primary" :loading="peerSaving" @tap="submitPeerRatings">
            提交互评
          </button>
        </view>

        <view v-if="detail.submission.files?.length" class="answers-review">
          <text class="section-title">{{ detail.submission.is_returned ? "上次提交的附件" : "我的附件" }}</text>
          <view class="files-row files-row--padded">
            <view
              v-for="file in detail.submission.files"
              :key="file.id"
              class="file-chip"
              @tap="previewSubmissionFile(file)"
            >
              <text class="file-chip__icon">{{ file.is_image ? "🖼️" : "📄" }}</text>
              <text class="file-chip__name">{{ file.file_name }}</text>
            </view>
          </view>
        </view>

        <view v-if="detail.submission.answers?.length" class="answers-review">
          <text class="section-title">{{ detail.submission.is_returned ? "上次提交的作答" : "我的作答" }}</text>
          <view v-for="(item, index) in detail.submission.answers" :key="index" class="review-item">
            <text class="review-item__q">{{ index + 1 }}. {{ item.question }}</text>
            <text class="review-item__a">{{ item.answer || "（未作答）" }}</text>
          </view>
        </view>
      </template>

      <!-- 作答模式：试卷 -->
      <template v-if="isAnswerMode && detail.paper">
        <view v-for="(page, pageIndex) in detail.paper.pages" :key="pageIndex" class="page-block">
          <text v-if="page.name" class="section-title">{{ page.name }}</text>
          <view v-for="(q, qIndex) in page.questions" :key="q.id" class="question-card glass-card">
            <view class="question-card__head">
              <text class="question-card__no">{{ qIndex + 1 }}</text>
              <text class="question-card__text">{{ q.text }}</text>
            </view>

            <view v-if="isChoice(q)" class="options">
              <view
                v-for="option in q.options"
                :key="option"
                class="option"
                :class="{
                  'option--selected':
                    q.type === 'radio' ? answers[q.id] === option : checkboxSelected(q, option),
                }"
                @tap="tapOption(q, option)"
              >
                <text>{{ option }}</text>
              </view>
            </view>

            <input
              v-else-if="q.type === 'text'"
              class="text-input"
              :value="answers[q.id] || ''"
              :disabled="controlsLocked"
              :placeholder="q.placeholder || '请输入答案'"
              @input="onTextInput(q.id, $event as never)"
            />

            <view v-else>
              <textarea
                class="textarea-input"
                :value="answers[q.id] || ''"
                :disabled="controlsLocked"
                :placeholder="q.placeholder || '请输入答案'"
                :maxlength="-1"
                auto-height
                @input="onTextInput(q.id, $event as never)"
              />

              <view v-if="isAttachmentQuestion(q)" class="uploads">
                <view v-if="(questionFiles[q.id] || []).length" class="files-row">
                  <template v-for="file in questionFiles[q.id]" :key="file.id">
                    <image
                      v-if="localPreview[file.relative_path]"
                      class="file-thumb"
                      :src="localPreview[file.relative_path]"
                      mode="aspectFill"
                      @tap="previewDraftFile(file)"
                    />
                    <view v-else class="file-chip" @tap="previewDraftFile(file)">
                      <text class="file-chip__icon">{{ file.is_image ? "🖼️" : "📄" }}</text>
                      <text class="file-chip__name">{{ file.file_name }}</text>
                    </view>
                  </template>
                </view>
                <view class="upload-btns">
                  <view
                    class="upload-btn"
                    :class="{ 'upload-btn--busy': uploadingQid === q.id }"
                    @tap="addPhotos(q)"
                  >
                    <text>{{ uploadingQid === q.id ? "上传中…" : "📷 拍照/选图" }}</text>
                  </view>
                  <view
                    class="upload-btn"
                    :class="{ 'upload-btn--busy': uploadingQid === q.id }"
                    @tap="addChatFiles(q)"
                  >
                    <text>📁 选择文件</text>
                  </view>
                  <view
                    v-if="(questionFiles[q.id] || []).length"
                    class="upload-btn upload-btn--danger"
                    @tap="clearFiles(q)"
                  >
                    <text>清空</text>
                  </view>
                </view>
                <text class="attach-hint">文件请先发送到微信任意聊天（如"文件传输助手"），再从聊天记录中选取</text>
              </view>
            </view>
          </view>
        </view>
      </template>

      <!-- 作答模式：普通作业 -->
      <template v-else-if="isAnswerMode">
        <view class="question-card glass-card">
          <textarea
            class="textarea-input textarea-input--large"
            :value="plainAnswer"
            :disabled="controlsLocked"
            placeholder="在这里输入你的作答内容…"
            :maxlength="-1"
            auto-height
            @input="onPlainInput($event as never)"
          />
          <view class="uploads">
            <view v-if="(questionFiles[PLAIN_FILE_QID] || []).length" class="files-row">
              <template v-for="file in questionFiles[PLAIN_FILE_QID]" :key="file.id">
                <image
                  v-if="localPreview[file.relative_path]"
                  class="file-thumb"
                  :src="localPreview[file.relative_path]"
                  mode="aspectFill"
                  @tap="previewDraftFile(file)"
                />
                <view v-else class="file-chip" @tap="previewDraftFile(file)">
                  <text class="file-chip__icon">{{ file.is_image ? "🖼️" : "📄" }}</text>
                  <text class="file-chip__name">{{ file.file_name }}</text>
                </view>
              </template>
            </view>
            <view class="upload-btns">
              <view
                class="upload-btn"
                :class="{ 'upload-btn--busy': uploadingQid === PLAIN_FILE_QID }"
                @tap="addPhotos(plainFileQuestion)"
              >
                <text>{{ uploadingQid === PLAIN_FILE_QID ? "上传中…" : "📷 拍照/选图" }}</text>
              </view>
              <view
                class="upload-btn"
                :class="{ 'upload-btn--busy': uploadingQid === PLAIN_FILE_QID }"
                @tap="addChatFiles(plainFileQuestion)"
              >
                <text>📁 选择文件</text>
              </view>
              <view
                v-if="(questionFiles[PLAIN_FILE_QID] || []).length"
                class="upload-btn upload-btn--danger"
                @tap="clearFiles(plainFileQuestion)"
              >
                <text>清空</text>
              </view>
            </view>
            <text class="attach-hint">代码/文档等文件请先发送到微信任意聊天（如"文件传输助手"），再从聊天记录中选取</text>
          </view>
        </view>
      </template>

      <view v-else-if="submitConfirmed && !detail.submission" class="empty" @tap="loadDetail">
        <text>提交已成功，点击刷新查看最新结果</text>
      </view>

      <!-- 已截止且无提交 -->
      <view v-else-if="!hasAnswerSubmission(detail.submission)" class="empty">
        <text>已超过提交时间{{ detail.assignment.late_policy_label ? `（${detail.assignment.late_policy_label}）` : "" }}</text>
      </view>

      <!-- 底部提交条 -->
      <view v-if="isAnswerMode || pendingSubmissionVersion !== null" class="submit-bar">
        <view class="submit-bar__info">
          <text v-if="detail.paper" class="submit-bar__progress">
            已答 {{ answeredCount }}/{{ allQuestions.length }}
          </text>
          <text v-if="draftSavedAt" class="submit-bar__draft">草稿已存 {{ draftSavedAt }}</text>
          <text v-if="draftStatus" class="submit-bar__draft">{{ draftStatus }}</text>
        </view>
        <button class="submit-bar__btn glass-btn-primary" :loading="submitting || checkingResult" :disabled="submitting || checkingResult || Boolean(uploadingQid) || selectingFiles" @tap="submit">{{ pendingSubmissionVersion !== null ? "核对提交结果" : uploadingQid ? "附件上传中" : detail.submission?.is_returned ? "重新提交" : "提交" }}</button>
      </view>
    </template>
  </view>
</template>

<style scoped>
.detail {
  min-height: 100vh;
  padding: 28rpx 28rpx 200rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.empty {
  padding: 120rpx 40rpx;
  text-align: center;
  color: #94a3b8;
  font-size: 28rpx;
}

.return-card {
  padding: 28rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
  color: #925a20;
  font-size: 26rpx;
  background: #fff8eb;
}

.section-title {
  font-size: 28rpx;
  font-weight: 600;
  color: #475569;
  padding: 8rpx 4rpx;
}

.head-card {
  padding: 36rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.head-card__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.head-card__badge {
  font-size: 22rpx;
  color: #4a7dff;
  background: rgba(74, 125, 255, 0.12);
  border-radius: 10rpx;
  padding: 4rpx 14rpx;
}

.head-card__badge--exam {
  color: #e0662f;
  background: rgba(224, 102, 47, 0.12);
}

.head-card__countdown {
  font-size: 26rpx;
  font-weight: 700;
  color: #e0662f;
}

.head-card__title {
  font-size: 36rpx;
  font-weight: 700;
  color: #16213a;
  line-height: 1.4;
}

.head-card__req {
  font-size: 26rpx;
  color: #64748b;
  line-height: 1.7;
}

.page-block {
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.question-card {
  padding: 32rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.question-card__head {
  display: flex;
  gap: 16rpx;
  align-items: flex-start;
}

.question-card__no {
  min-width: 44rpx;
  height: 44rpx;
  border-radius: 12rpx;
  background: #eef2ff;
  color: #4a7dff;
  font-size: 24rpx;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.question-card__text {
  flex: 1;
  font-size: 30rpx;
  color: #16213a;
  line-height: 1.6;
}

.options {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.option {
  border: 2rpx solid rgba(140, 158, 210, 0.28);
  background: rgba(255, 255, 255, 0.4);
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  font-size: 28rpx;
  color: #334155;
  line-height: 1.5;
}

.option--selected {
  border-color: #4a7dff;
  background: linear-gradient(135deg, rgba(91, 140, 255, 0.14), rgba(74, 125, 255, 0.08));
  color: #1d4ed8;
  font-weight: 600;
}

.text-input {
  border: 2rpx solid #e2e8f0;
  border-radius: 20rpx;
  padding: 22rpx 26rpx;
  font-size: 28rpx;
  min-height: 56rpx;
}

.textarea-input {
  width: 100%;
  box-sizing: border-box;
  border: 2rpx solid #e2e8f0;
  border-radius: 20rpx;
  padding: 24rpx 26rpx;
  font-size: 28rpx;
  line-height: 1.7;
  min-height: 160rpx;
}

.textarea-input--large {
  min-height: 400rpx;
}

.attach-hint {
  margin-top: 12rpx;
  font-size: 22rpx;
  color: #94a3b8;
}

.uploads {
  margin-top: 20rpx;
  display: flex;
  flex-direction: column;
  gap: 18rpx;
}

.files-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16rpx;
}

.files-row--padded {
  background: rgba(255, 255, 255, 0.55);
  border-radius: 24rpx;
  padding: 24rpx;
}

.file-thumb {
  width: 160rpx;
  height: 160rpx;
  border-radius: 16rpx;
  background: #f1f5f9;
}

.file-chip {
  max-width: 100%;
  display: flex;
  align-items: center;
  gap: 10rpx;
  background: #f1f5f9;
  border-radius: 14rpx;
  padding: 14rpx 20rpx;
}

.file-chip__icon {
  font-size: 28rpx;
}

.file-chip__name {
  font-size: 22rpx;
  color: #475569;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 360rpx;
}

.upload-btns {
  display: flex;
  gap: 16rpx;
}

.upload-btn {
  min-height: 72rpx;
  display: flex;
  align-items: center;
  padding: 0 30rpx;
  border-radius: 999rpx;
  background: rgba(74, 125, 255, 0.1);
  color: #1d4ed8;
  font-size: 26rpx;
}

.upload-btn--busy {
  opacity: 0.6;
}

.upload-btn--danger {
  background: rgba(239, 68, 68, 0.08);
  color: #dc2626;
}

.result-card {
  padding: 40rpx 36rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.result-card__score-row {
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}

.result-card__score {
  font-size: 72rpx;
  font-weight: 800;
  color: #16213a;
}

.result-card__score-label {
  font-size: 26rpx;
  color: #94a3b8;
}

.result-card__meta {
  font-size: 24rpx;
  color: #94a3b8;
}

.result-card__feedback {
  margin-top: 8rpx;
  background: #f8fafc;
  border-radius: 20rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.result-card__feedback-title {
  font-size: 24rpx;
  font-weight: 600;
  color: #475569;
}

.result-card__feedback-text {
  font-size: 26rpx;
  color: #334155;
  line-height: 1.7;
}

.peer-card {
  padding: 36rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.peer-card__hint {
  font-size: 22rpx;
  color: #94a3b8;
  line-height: 1.6;
}

.peer-row {
  display: flex;
  align-items: center;
  gap: 20rpx;
}

.peer-row__name {
  flex: 0 0 140rpx;
  font-size: 28rpx;
  color: #16213a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.peer-row__slider {
  flex: 1;
  margin: 0;
}

.peer-card__submit {
  min-height: 84rpx;
  font-size: 28rpx;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 8rpx 0 0;
}

.answers-review {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.review-item {
  background: rgba(255, 255, 255, 0.55);
  border: 1rpx solid rgba(255, 255, 255, 0.7);
  border-radius: 24rpx;
  padding: 28rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.review-item__q {
  font-size: 26rpx;
  color: #475569;
  line-height: 1.6;
}

.review-item__a {
  font-size: 28rpx;
  color: #16213a;
  line-height: 1.6;
}

.submit-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(20px) saturate(1.4);
  -webkit-backdrop-filter: blur(20px) saturate(1.4);
  border-top: 1rpx solid rgba(255, 255, 255, 0.8);
  padding: 20rpx 32rpx calc(env(safe-area-inset-bottom) + 20rpx);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24rpx;
  box-shadow: 0 -8rpx 32rpx rgba(15, 23, 42, 0.08);
}

.submit-bar__info {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.submit-bar__progress {
  font-size: 26rpx;
  font-weight: 600;
  color: #16213a;
}

.submit-bar__draft {
  font-size: 20rpx;
  color: #94a3b8;
}

.submit-bar__btn {
  min-width: 240rpx;
  min-height: 88rpx;
  font-size: 30rpx;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
}
</style>
