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
 * - 附件题：本批次仅文字作答 + 提示去网页端传附件（拍照上传下批实现）。
 */
import { onHide, onLoad, onUnload } from "@dcloudio/uni-app";
import { computed, reactive, ref } from "vue";

import { request } from "../../utils/api";

interface Question {
  id: string;
  type: string;
  text: string;
  options?: string[];
  placeholder?: string;
}

interface DetailData {
  assignment: {
    id: number | string;
    title: string;
    requirements_md: string;
    is_exam: boolean;
    due_at: string | null;
    remaining_seconds: number | null;
    availability_mode_label: string;
    is_accepting_submissions: boolean;
    late_policy_label: string;
  };
  paper: { title: string; description: string; pages?: Array<{ name?: string; questions?: Question[] }> } | null;
  submission: {
    status: string;
    score: number | null;
    feedback_md: string;
    submitted_at: string;
    answers: Array<{ question_id?: string; question?: string; answer?: string }>;
  } | null;
}

const CHECKBOX_SEP = "|||";
const DRAFT_INTERVAL_MS = 30_000;
const PLAIN_QUESTION_LABEL = "作答";

const assignmentId = ref("");
const detail = ref<DetailData | null>(null);
const loading = ref(true);
const failed = ref(false);
const errorMessage = ref("");
const answers = reactive<Record<string, string>>({});
const plainAnswer = ref("");
const submitting = ref(false);
const remainingSeconds = ref<number | null>(null);
const draftSavedAt = ref("");

let draftTimer: ReturnType<typeof setInterval> | null = null;
let countdownTimer: ReturnType<typeof setInterval> | null = null;
let startedAt = "";
let lastDraftSignature = "";

const localDraftKey = computed(() => `lanshareTaskDraft:${assignmentId.value}`);

const allQuestions = computed<Question[]>(() => {
  const pages = detail.value?.paper?.pages ?? [];
  return pages.flatMap((page) => page.questions ?? []);
});

const isAnswerMode = computed(
  () => Boolean(detail.value && !detail.value.submission && detail.value.assignment.is_accepting_submissions),
);

const answeredCount = computed(
  () => allQuestions.value.filter((q) => (answers[q.id] || "").trim()).length,
);

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

function checkboxSelected(q: Question, option: string): boolean {
  return (answers[q.id] || "").split(CHECKBOX_SEP).includes(option);
}

function tapOption(q: Question, option: string): void {
  if (!isAnswerMode.value) return;
  if (q.type === "radio") {
    answers[q.id] = answers[q.id] === option ? "" : option;
  } else {
    const current = (answers[q.id] || "").split(CHECKBOX_SEP).filter(Boolean);
    const next = current.includes(option)
      ? current.filter((item) => item !== option)
      : [...current, option];
    answers[q.id] = next.join(CHECKBOX_SEP);
  }
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
        attachments: [],
      })),
    });
  }
  return JSON.stringify({
    answers: [{ question: PLAIN_QUESTION_LABEL, answer: plainAnswer.value }],
  });
}

// ---------- 草稿 ----------

function saveLocalDraft(): void {
  try {
    uni.setStorageSync(localDraftKey.value, {
      answers: { ...answers },
      plain: plainAnswer.value,
      saved_at: new Date().toISOString(),
    });
  } catch {
    /* 本地草稿失败不阻断作答 */
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
    };
    if (local && typeof local === "object") {
      Object.assign(answers, local.answers || {});
      plainAnswer.value = local.plain || "";
      localSavedAt = local.saved_at || "";
    }
  } catch {
    /* ignore */
  }
  try {
    const draft = await request<{
      exists?: boolean;
      answers_json?: string;
      server_updated_at?: string;
    }>({ path: `/api/assignments/${assignmentId.value}/draft` });
    if (draft?.exists && draft.answers_json) {
      const serverTime = Date.parse(draft.server_updated_at || "") || 0;
      const localTime = Date.parse(localSavedAt) || 0;
      if (serverTime >= localTime) {
        const parsed = JSON.parse(draft.answers_json) as { answers?: [] };
        restoreFromAnswersList(parsed.answers || []);
      }
    }
  } catch {
    /* 服务器草稿失败回落本地草稿 */
  }
}

async function saveServerDraft(): Promise<void> {
  if (!isAnswerMode.value) return;
  const payload = buildAnswersJson();
  if (payload === lastDraftSignature) return;
  try {
    await request({
      path: `/api/assignments/${assignmentId.value}/draft`,
      method: "POST",
      form: true,
      data: { answers_json: payload, current_page: 0 },
    });
    lastDraftSignature = payload;
    draftSavedAt.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    /* 弱网时静默，本地草稿仍在 */
  }
}

// ---------- 倒计时 ----------

function startCountdown(): void {
  if (countdownTimer) clearInterval(countdownTimer);
  if (remainingSeconds.value === null) return;
  countdownTimer = setInterval(() => {
    if (remainingSeconds.value === null) return;
    remainingSeconds.value = Math.max(0, remainingSeconds.value - 1);
    if (remainingSeconds.value === 0 && countdownTimer) {
      clearInterval(countdownTimer);
      void saveServerDraft();
      uni.showModal({
        title: "时间到",
        content: "作答时间已截止，草稿已保存。请联系教师确认是否可补交。",
        showCancel: false,
      });
      void loadDetail();
    }
  }, 1000);
}

// ---------- 加载与提交 ----------

async function loadDetail(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    detail.value = await request<DetailData>({
      path: `/api/mp/tasks/assignment/${assignmentId.value}`,
    });
    remainingSeconds.value = detail.value.assignment.remaining_seconds;
    if (detail.value.submission) {
      restoreFromAnswersList(detail.value.submission.answers || []);
    } else if (detail.value.assignment.is_accepting_submissions) {
      await restoreDrafts();
      startCountdown();
    }
  } catch (error: unknown) {
    failed.value = true;
    errorMessage.value = error instanceof Error ? error.message : "加载失败";
    if ((error as { statusCode?: number }).statusCode === 401) {
      uni.reLaunch({ url: "/pages/welcome/index" });
    }
  } finally {
    loading.value = false;
  }
}

async function submit(): Promise<void> {
  if (submitting.value || !detail.value) return;
  const hasContent = detail.value.paper
    ? answeredCount.value > 0
    : Boolean(plainAnswer.value.trim());
  if (!hasContent) {
    uni.showToast({ title: "还没有填写任何作答内容", icon: "none" });
    return;
  }
  const unanswered = detail.value.paper
    ? allQuestions.value.length - answeredCount.value
    : 0;
  const confirmed = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: "确认提交",
      content: unanswered > 0 ? `还有 ${unanswered} 题未作答，确定提交吗？` : "提交后将不能再修改，确定提交吗？",
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
  if (!confirmed) return;

  submitting.value = true;
  try {
    await request({
      path: `/api/assignments/${assignmentId.value}/submit`,
      method: "POST",
      form: true,
      data: { answers_json: buildAnswersJson(), started_at: startedAt },
    });
    try {
      uni.removeStorageSync(localDraftKey.value);
    } catch {
      /* ignore */
    }
    uni.showToast({ title: "提交成功", icon: "success" });
    await loadDetail();
  } catch (error: unknown) {
    uni.showModal({
      title: "提交失败",
      content: error instanceof Error ? error.message : "网络异常，作答内容已在草稿中，请稍后重试。",
      showCancel: false,
    });
    void saveServerDraft();
  } finally {
    submitting.value = false;
  }
}

function onTextInput(qid: string, event: { detail: { value: string } }): void {
  answers[qid] = event.detail.value;
  saveLocalDraft();
}

function onPlainInput(event: { detail: { value: string } }): void {
  plainAnswer.value = event.detail.value;
  saveLocalDraft();
}

onLoad((query) => {
  assignmentId.value = String((query as Record<string, string>)?.id || "");
  startedAt = new Date().toISOString();
  if (!assignmentId.value) {
    failed.value = true;
    errorMessage.value = "缺少任务参数";
    loading.value = false;
    return;
  }
  void loadDetail();
  draftTimer = setInterval(() => void saveServerDraft(), DRAFT_INTERVAL_MS);
});

onHide(() => {
  saveLocalDraft();
  void saveServerDraft();
});

onUnload(() => {
  if (draftTimer) clearInterval(draftTimer);
  if (countdownTimer) clearInterval(countdownTimer);
  saveLocalDraft();
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
      <view class="head-card">
        <view class="head-card__top">
          <text class="head-card__badge" :class="{ 'head-card__badge--exam': detail.assignment.is_exam }">
            {{ detail.assignment.is_exam ? "考试" : "作业" }}
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

      <!-- 结果视图 -->
      <template v-if="detail.submission">
        <view class="result-card">
          <view class="result-card__score-row">
            <text class="result-card__score">
              {{ detail.submission.score !== null && detail.submission.score !== undefined ? detail.submission.score : "—" }}
            </text>
            <text class="result-card__score-label">
              {{ detail.submission.score !== null && detail.submission.score !== undefined ? "得分" : "待批改" }}
            </text>
          </view>
          <text class="result-card__meta">提交于 {{ detail.submission.submitted_at }}</text>
          <view v-if="detail.submission.feedback_md" class="result-card__feedback">
            <text class="result-card__feedback-title">教师批语</text>
            <text class="result-card__feedback-text">{{ detail.submission.feedback_md }}</text>
          </view>
        </view>

        <view v-if="detail.submission.answers?.length" class="answers-review">
          <text class="section-title">我的作答</text>
          <view v-for="(item, index) in detail.submission.answers" :key="index" class="review-item">
            <text class="review-item__q">{{ index + 1 }}. {{ item.question }}</text>
            <text class="review-item__a">{{ item.answer || "（未作答）" }}</text>
          </view>
        </view>
      </template>

      <!-- 作答模式：试卷 -->
      <template v-else-if="isAnswerMode && detail.paper">
        <view v-for="(page, pageIndex) in detail.paper.pages" :key="pageIndex" class="page-block">
          <text v-if="page.name" class="section-title">{{ page.name }}</text>
          <view v-for="(q, qIndex) in page.questions" :key="q.id" class="question-card">
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
              :placeholder="q.placeholder || '请输入答案'"
              @input="onTextInput(q.id, $event as never)"
            />

            <view v-else>
              <textarea
                class="textarea-input"
                :value="answers[q.id] || ''"
                :placeholder="q.placeholder || '请输入答案'"
                :maxlength="-1"
                auto-height
                @input="onTextInput(q.id, $event as never)"
              />
              <text v-if="q.type !== 'textarea'" class="attach-hint">
                📎 此题如需上传附件/绘图，请在网页端完成
              </text>
            </view>
          </view>
        </view>
      </template>

      <!-- 作答模式：普通作业 -->
      <template v-else-if="isAnswerMode">
        <view class="question-card">
          <textarea
            class="textarea-input textarea-input--large"
            :value="plainAnswer"
            placeholder="在这里输入你的作答内容…"
            :maxlength="-1"
            auto-height
            @input="onPlainInput($event as never)"
          />
          <text class="attach-hint">📎 如需上传文件附件，请在网页端完成</text>
        </view>
      </template>

      <!-- 已截止且无提交 -->
      <view v-else class="empty">
        <text>已超过提交时间{{ detail.assignment.late_policy_label ? `（${detail.assignment.late_policy_label}）` : "" }}</text>
      </view>

      <!-- 底部提交条 -->
      <view v-if="isAnswerMode" class="submit-bar">
        <view class="submit-bar__info">
          <text v-if="detail.paper" class="submit-bar__progress">
            已答 {{ answeredCount }}/{{ allQuestions.length }}
          </text>
          <text v-if="draftSavedAt" class="submit-bar__draft">草稿已存 {{ draftSavedAt }}</text>
        </view>
        <button class="submit-bar__btn" :loading="submitting" @tap="submit">提交</button>
      </view>
    </template>
  </view>
</template>

<style scoped>
.detail {
  min-height: 100vh;
  padding: 28rpx 28rpx 200rpx;
  background: #f4f6fb;
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

.section-title {
  font-size: 28rpx;
  font-weight: 600;
  color: #475569;
  padding: 8rpx 4rpx;
}

.head-card {
  background: #ffffff;
  border-radius: 32rpx;
  padding: 36rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
  box-shadow: 0 8rpx 32rpx rgba(15, 23, 42, 0.05);
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
  background: #ffffff;
  border-radius: 28rpx;
  padding: 32rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
  box-shadow: 0 6rpx 24rpx rgba(15, 23, 42, 0.04);
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
  border: 2rpx solid #e2e8f0;
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  font-size: 28rpx;
  color: #334155;
  line-height: 1.5;
}

.option--selected {
  border-color: #4a7dff;
  background: rgba(74, 125, 255, 0.08);
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

.result-card {
  background: #ffffff;
  border-radius: 32rpx;
  padding: 40rpx 36rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  box-shadow: 0 8rpx 32rpx rgba(15, 23, 42, 0.05);
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

.answers-review {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.review-item {
  background: #ffffff;
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
  background: #ffffff;
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
  border-radius: 999rpx;
  background: #4a7dff;
  color: #ffffff;
  font-size: 30rpx;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
}
</style>
