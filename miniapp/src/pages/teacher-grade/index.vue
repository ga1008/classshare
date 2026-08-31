<script setup lang="ts">
/**
 * 教师批阅页 v2（逐题视图）。
 *
 * 结构：顶部试卷/学生/最终分 → 左侧可收起题目列表（客观判定着色）
 * → 主区当前题（题干/选项/学生答案/标准答案/本题附件弹层）→ 评分/评语
 * → 底部固定操作栏（上一位/保存/保存并下一位）。
 *
 * 数据源：
 * - GET /api/mp/teacher/assignment/{id}/grading      名单队列与统计
 * - GET /api/mp/teacher/submission/{sid}/review      逐题聚合（新）
 * - POST /api/submissions/{id}/grade                 打分（迟交罚分/AI冲正/
 *   修订台账/小组分联动全在服务端，此处只传原始分+评语）
 */
import { onLoad } from "@dcloudio/uni-app";
import { computed, reactive, ref, watch } from "vue";

import { request } from "../../utils/api";
import { downloadProtectedTempFile, previewProtectedFile } from "../../utils/preview";

interface FeedbackBlock {
  type: string;
  text: string;
}

interface GradeFile {
  id: number;
  file_name: string;
  mime_type: string;
  file_size: number | null;
  is_image: boolean;
}

interface QueueEntry {
  submission_id: number | null;
  student_pk_id: number;
  student_name: string;
  student_id_number: string;
  status: string;
  status_label: string;
  score: number | null;
  is_late: boolean;
}

interface ReviewQuestion {
  no: number;
  question_id: string;
  type: string;
  type_label: string;
  text: string;
  options: string[];
  points: number;
  standard_answer: string;
  student_answer: string;
  verdict: "full" | "partial" | "zero" | "blank" | "doubt" | "manual";
  verdict_label: string;
  earned: number | null;
  score_display: string;
  attachments: GradeFile[];
}

interface ReviewData {
  assignment: { id: number; title: string; is_exam: boolean; course_name: string; class_name: string };
  student: { name: string; student_id_number: string };
  submission: {
    id: number;
    status: string;
    status_label: string;
    score: number | null;
    score_before_late_penalty: number | null;
    late_penalty_points: number;
    is_late: boolean;
    resubmission_allowed: boolean;
    submitted_at: string;
    feedback_md: string;
    feedback_blocks: FeedbackBlock[];
  };
  questions: ReviewQuestion[];
  paper_files: GradeFile[];
  total_points: number;
}

const QUICK_SCORES = [60, 70, 80, 85, 90, 95, 100];

const VERDICT_COLORS: Record<string, string> = {
  full: "#1e9e6a",
  partial: "#d97706",
  zero: "#e5484d",
  blank: "#8b5cf6",
  doubt: "#0284c7",
  manual: "#8b96b3",
};

const assignmentId = ref("");
const queue = ref<QueueEntry[]>([]);
const loading = ref(true);
const failed = ref(false);
const index = ref(0);

const review = ref<ReviewData | null>(null);
const reviewLoading = ref(false);
const reviewCache = new Map<number, ReviewData>();

/** 当前题下标；-1 = 整卷附件视图 */
const qIndex = ref(0);
const drawerOpen = ref(false);
const attachPanelOpen = ref(false);

const saving = ref(false);
const editingFeedback = ref(false);
const gradeScore = ref("");
const gradeFeedback = ref("");
const thumbPaths = reactive<Record<number, string>>({});

const current = computed<QueueEntry | null>(() => queue.value[index.value] ?? null);
const hasPrev = computed(() => index.value > 0);
const hasNext = computed(() => index.value < queue.value.length - 1);

const questions = computed<ReviewQuestion[]>(() => review.value?.questions ?? []);
const currentQuestion = computed<ReviewQuestion | null>(
  () => (qIndex.value >= 0 ? questions.value[qIndex.value] ?? null : null),
);
const paperFiles = computed<GradeFile[]>(() => review.value?.paper_files ?? []);
const attachTarget = computed<GradeFile[]>(() =>
  qIndex.value === -1 ? paperFiles.value : currentQuestion.value?.attachments ?? [],
);

const canGrade = computed(() => {
  const sub = review.value?.submission;
  return Boolean(sub && !sub.resubmission_allowed);
});

const scoreNote = computed(() => {
  const sub = review.value?.submission;
  if (!sub || !sub.is_late) return "";
  if (sub.score_before_late_penalty !== null && sub.score_before_late_penalty !== undefined) {
    return `原 ${sub.score_before_late_penalty} 分，迟交扣 ${sub.late_penalty_points} 分`;
  }
  return "迟交提交，保存时按迟交策略自动计分";
});

const isDirty = computed(() => {
  const sub = review.value?.submission;
  if (!sub) return false;
  const savedScore = sub.score !== null && sub.score !== undefined ? String(sub.score) : "";
  return gradeScore.value !== savedScore || gradeFeedback.value !== (sub.feedback_md || "");
});

function syncForm(): void {
  editingFeedback.value = false;
  const sub = review.value?.submission;
  if (!sub) {
    gradeScore.value = "";
    gradeFeedback.value = "";
    return;
  }
  gradeScore.value = sub.score !== null && sub.score !== undefined ? String(sub.score) : "";
  gradeFeedback.value = sub.feedback_md || "";
}

async function loadQueue(): Promise<void> {
  const data = await request<{ entries: QueueEntry[] }>({
    path: `/api/mp/teacher/assignment/${assignmentId.value}/grading`,
  });
  queue.value = (data.entries ?? []).filter(
    (entry) => entry.status !== "unsubmitted" && entry.submission_id,
  );
}

async function loadReview(force = false): Promise<void> {
  const sid = current.value?.submission_id;
  if (!sid) {
    review.value = null;
    return;
  }
  if (!force && reviewCache.has(sid)) {
    review.value = reviewCache.get(sid) ?? null;
    qIndex.value = review.value?.questions.length ? 0 : -1;
    syncForm();
    return;
  }
  reviewLoading.value = true;
  try {
    const data = await request<ReviewData>({ path: `/api/mp/teacher/submission/${sid}/review` });
    reviewCache.set(sid, data);
    review.value = data;
    qIndex.value = data.questions.length ? 0 : -1;
    syncForm();
    void loadThumbnails();
  } catch (error: unknown) {
    if ((error as { statusCode?: number }).statusCode === 401) {
      uni.reLaunch({ url: "/pages/welcome/index" });
      return;
    }
    uni.showToast({ title: "作答加载失败", icon: "none" });
  } finally {
    reviewLoading.value = false;
  }
}

async function loadThumbnails(): Promise<void> {
  const files = [...attachTarget.value, ...paperFiles.value].filter(
    (file) => file.is_image && !thumbPaths[file.id],
  );
  for (const file of files) {
    try {
      thumbPaths[file.id] = await downloadProtectedTempFile(`/submissions/download/${file.id}`);
    } catch {
      /* 缩略图失败退化占位，点击仍可走完整预览 */
    }
  }
}

async function loadAll(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    await loadQueue();
    await loadReview();
  } catch (error: unknown) {
    failed.value = true;
    if ((error as { statusCode?: number }).statusCode === 401) {
      uni.reLaunch({ url: "/pages/welcome/index" });
    }
  } finally {
    loading.value = false;
  }
}

watch(current, () => {
  void loadReview();
});

watch(attachPanelOpen, (open) => {
  if (open) void loadThumbnails();
});

// ---------- 题目导航 ----------

function selectQuestion(i: number): void {
  qIndex.value = i;
  drawerOpen.value = false;
  attachPanelOpen.value = false;
}

function qPrev(): void {
  if (qIndex.value > 0) qIndex.value -= 1;
  else if (qIndex.value === -1 && questions.value.length) qIndex.value = questions.value.length - 1;
}

function qNext(): void {
  if (qIndex.value >= 0 && qIndex.value < questions.value.length - 1) qIndex.value += 1;
  else if (qIndex.value === questions.value.length - 1 && paperFiles.value.length) qIndex.value = -1;
}

// ---------- 学生导航 ----------

function confirmDiscard(): Promise<boolean> {
  if (!isDirty.value) return Promise.resolve(true);
  return new Promise((resolve) => {
    uni.showModal({
      title: "未保存的修改",
      content: "当前学生的评分修改还没保存，离开将丢失。",
      confirmText: "仍然离开",
      cancelText: "留在本页",
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
}

async function goPrev(): Promise<void> {
  if (!hasPrev.value || !(await confirmDiscard())) return;
  index.value -= 1;
}

async function goNext(): Promise<void> {
  if (!hasNext.value || !(await confirmDiscard())) return;
  index.value += 1;
}

// ---------- 打分 ----------

function applyQuickScore(score: number): void {
  gradeScore.value = String(score);
}

async function saveGrade(advance: boolean): Promise<void> {
  const sid = current.value?.submission_id;
  if (!sid || saving.value) return;
  if (!canGrade.value) {
    uni.showToast({ title: "该提交已退回待重交，不能批改", icon: "none" });
    return;
  }
  const score = Number(gradeScore.value);
  if (!Number.isFinite(score) || score < 0 || score > 100) {
    uni.showToast({ title: "请输入 0-100 的分数", icon: "none" });
    return;
  }
  saving.value = true;
  try {
    await request({
      path: `/api/submissions/${sid}/grade`,
      method: "POST",
      data: { score, feedback_md: gradeFeedback.value },
    });
    uni.showToast({ title: "已保存", icon: "success" });
    reviewCache.delete(sid);
    await loadQueue();
    await loadReview(true);
    if (advance && hasNext.value) {
      index.value += 1;
    }
  } catch (error: unknown) {
    uni.showModal({
      title: "保存失败",
      content: error instanceof Error ? error.message : "网络异常，请重试。",
      showCancel: false,
    });
  } finally {
    saving.value = false;
  }
}

// ---------- 附件 ----------

function previewFile(file: GradeFile): void {
  void previewProtectedFile({
    path: `/submissions/download/${file.id}`,
    fileName: file.file_name,
    mimeType: file.mime_type,
    localPath: thumbPaths[file.id],
  });
}

function formatSize(size: number | null): string {
  if (!size) return "";
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))}KB`;
  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

onLoad((query) => {
  const params = (query ?? {}) as Record<string, string>;
  assignmentId.value = String(params.id || "");
  const targetSid = Number(params.sid || 0);
  if (!assignmentId.value) {
    failed.value = true;
    loading.value = false;
    return;
  }
  void loadAll().then(() => {
    if (targetSid) {
      const found = queue.value.findIndex((entry) => entry.submission_id === targetSid);
      if (found >= 0 && found !== index.value) index.value = found;
    }
  });
});
</script>

<template>
  <view class="page">
    <view v-if="loading && !review" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="loadAll()"><text>加载失败，点击重试</text></view>
    <view v-else-if="!current" class="empty"><text>还没有可批阅的提交</text></view>

    <template v-else>
      <!-- 顶部：试卷信息 + 学生 + 最终分 -->
      <view class="header glass-card">
        <view class="header__paper">
          <text class="header__title">{{ review?.assignment.title || "" }}</text>
          <text class="header__meta">
            {{ review?.assignment.course_name }} · {{ review?.assignment.class_name }}
            <template v-if="review?.total_points"> · 卷面 {{ review.total_points }} 分</template>
          </text>
        </view>
        <view class="header__row">
          <view class="header__who">
            <text class="header__name">{{ current.student_name }}</text>
            <text class="header__sid">{{ current.student_id_number }} · {{ index + 1 }}/{{ queue.length }}</text>
          </view>
          <view class="header__score-box">
            <text
              class="header__score"
              :class="{ 'header__score--none': review?.submission.score === null }"
            >
              {{ review?.submission.score ?? "—" }}
            </text>
            <text class="header__status">{{ review?.submission.status_label }}</text>
          </view>
        </view>
        <view class="header__chips">
          <text v-if="review?.submission.is_late" class="chip chip--late">迟交</text>
          <text v-if="review?.submission.resubmission_allowed" class="chip chip--late">已退回待重交</text>
          <text v-if="scoreNote" class="chip">{{ scoreNote }}</text>
        </view>
      </view>

      <view v-if="reviewLoading" class="empty"><text>作答加载中…</text></view>

      <template v-else-if="review">
        <!-- 题目区 -->
        <view v-if="currentQuestion" class="card glass-card">
          <view class="q-head">
            <view class="q-head__left">
              <text class="q-head__no">第 {{ currentQuestion.no }} 题</text>
              <text class="q-head__type">
                {{ currentQuestion.type_label }}
                <template v-if="currentQuestion.points"> · {{ currentQuestion.points }} 分</template>
              </text>
            </view>
            <text
              class="verdict-chip"
              :style="{ color: VERDICT_COLORS[currentQuestion.verdict], borderColor: VERDICT_COLORS[currentQuestion.verdict] }"
            >
              <template v-if="currentQuestion.score_display">{{ currentQuestion.score_display }} · </template>{{ currentQuestion.verdict_label }}
            </text>
          </view>

          <text class="q-text">{{ currentQuestion.text }}</text>

          <view v-if="currentQuestion.options.length" class="q-options">
            <text v-for="(opt, i) in currentQuestion.options" :key="i" class="q-option">{{ opt }}</text>
          </view>

          <view class="answer-block answer-block--student">
            <text class="answer-block__label">学生答案</text>
            <text class="answer-block__text" :class="{ 'answer-block__text--empty': !currentQuestion.student_answer }">
              {{ currentQuestion.student_answer ? currentQuestion.student_answer.split("|||").join("、") : "（未作答）" }}
            </text>
          </view>

          <view v-if="currentQuestion.standard_answer" class="answer-block answer-block--standard">
            <text class="answer-block__label">标准答案</text>
            <text class="answer-block__text">{{ currentQuestion.standard_answer }}</text>
          </view>

          <view
            v-if="currentQuestion.attachments.length"
            class="attach-btn press"
            @tap="attachPanelOpen = true"
          >
            <text>📎 查看本题附件（{{ currentQuestion.attachments.length }}）</text>
          </view>

          <view class="q-nav">
            <view class="q-nav__btn press" :class="{ 'q-nav__btn--disabled': qIndex <= 0 }" @tap="qPrev">
              <text>‹ 上一题</text>
            </view>
            <text class="q-nav__pos">{{ qIndex + 1 }} / {{ questions.length }}</text>
            <view
              class="q-nav__btn press"
              :class="{ 'q-nav__btn--disabled': qIndex >= questions.length - 1 && !paperFiles.length }"
              @tap="qNext"
            >
              <text>下一题 ›</text>
            </view>
          </view>
        </view>

        <!-- 整卷附件视图 -->
        <view v-else class="card glass-card">
          <text class="card__title">整卷附件（{{ paperFiles.length }}）</text>
          <view v-if="paperFiles.some((f) => f.is_image)" class="thumb-grid">
            <view
              v-for="file in paperFiles.filter((f) => f.is_image)"
              :key="file.id"
              class="thumb"
              @tap="previewFile(file)"
            >
              <image v-if="thumbPaths[file.id]" class="thumb__img" :src="thumbPaths[file.id]" mode="aspectFill" />
              <view v-else class="thumb__placeholder"><text>🖼️</text></view>
            </view>
          </view>
          <view class="file-list">
            <view
              v-for="file in paperFiles.filter((f) => !f.is_image)"
              :key="file.id"
              class="file-chip press"
              @tap="previewFile(file)"
            >
              <text>📄</text>
              <text class="file-chip__name">{{ file.file_name }}</text>
              <text class="file-chip__size">{{ formatSize(file.file_size) }}</text>
            </view>
          </view>
          <view v-if="!paperFiles.length" class="empty-inline"><text>没有整卷附件</text></view>
        </view>

        <!-- 评分 -->
        <view class="card glass-card">
          <text class="card__title">评分</text>
          <view class="score-row">
            <input v-model="gradeScore" class="score-input" type="digit" placeholder="0-100" :disabled="!canGrade" />
            <view class="quick-scores">
              <view
                v-for="score in QUICK_SCORES"
                :key="score"
                class="quick-score"
                :class="{ 'quick-score--active': gradeScore === String(score) }"
                @tap="applyQuickScore(score)"
              >
                <text>{{ score }}</text>
              </view>
            </view>
          </view>
        </view>

        <!-- 评语 -->
        <view class="card glass-card">
          <view class="card__head">
            <text class="card__title">评语</text>
            <text class="card__action" @tap="editingFeedback = !editingFeedback">
              {{ editingFeedback ? "预览" : gradeFeedback ? "编辑" : "写评语" }}
            </text>
          </view>

          <textarea
            v-if="editingFeedback"
            v-model="gradeFeedback"
            class="feedback-editor"
            placeholder="支持 Markdown（标题/列表/加粗）"
            :maxlength="-1"
            auto-height
          />
          <template v-else>
            <view
              v-if="review.submission.feedback_blocks.length && gradeFeedback === review.submission.feedback_md"
              class="feedback"
            >
              <template v-for="(block, i) in review.submission.feedback_blocks" :key="i">
                <text v-if="block.type === 'h1' || block.type === 'h2'" class="fb fb--h2">{{ block.text }}</text>
                <text v-else-if="block.type === 'h3'" class="fb fb--h3">{{ block.text }}</text>
                <text v-else-if="block.type === 'li'" class="fb fb--li">· {{ block.text }}</text>
                <text v-else-if="block.type === 'strong'" class="fb fb--strong">{{ block.text }}</text>
                <text v-else class="fb">{{ block.text }}</text>
              </template>
            </view>
            <text v-else-if="gradeFeedback" class="feedback-raw">{{ gradeFeedback }}</text>
            <text v-else class="feedback-empty">暂无评语，点右上角"写评语"补充。</text>
          </template>
        </view>
      </template>

      <!-- 底部操作栏 -->
      <view class="bottom-spacer" />
      <view class="action-bar">
        <view class="nav-btn press" :class="{ 'nav-btn--disabled': !hasPrev }" @tap="goPrev">
          <text>‹</text>
        </view>
        <button class="save-btn save-btn--plain" :disabled="saving || !canGrade" @tap="saveGrade(false)">
          保存
        </button>
        <button
          class="save-btn glass-btn-primary"
          :loading="saving"
          :disabled="saving || !canGrade"
          @tap="saveGrade(true)"
        >
          {{ hasNext ? "保存并下一位" : "保存" }}
        </button>
        <view class="nav-btn press" :class="{ 'nav-btn--disabled': !hasNext }" @tap="goNext">
          <text>›</text>
        </view>
      </view>

      <!-- 左侧题目列表抽屉 -->
      <view class="drawer-fab press" @tap="drawerOpen = true">
        <text>☰</text>
        <text class="drawer-fab__label">题目</text>
      </view>
      <view v-if="drawerOpen" class="mask" @tap="drawerOpen = false" />
      <view class="drawer" :class="{ 'drawer--open': drawerOpen }">
        <text class="drawer__title">题目列表</text>
        <scroll-view class="drawer__scroll" scroll-y>
          <view
            v-for="(q, i) in questions"
            :key="q.question_id"
            class="drawer__item press"
            :class="{ 'drawer__item--active': qIndex === i }"
            @tap="selectQuestion(i)"
          >
            <text class="drawer__dot" :style="{ background: VERDICT_COLORS[q.verdict] }" />
            <text class="drawer__no">{{ q.no }}</text>
            <view class="drawer__body">
              <text class="drawer__type">{{ q.type_label }}</text>
              <text class="drawer__verdict" :style="{ color: VERDICT_COLORS[q.verdict] }">
                <template v-if="q.score_display">{{ q.score_display }} · </template>{{ q.verdict_label }}
              </text>
            </view>
            <text v-if="q.attachments.length" class="drawer__attach">📎{{ q.attachments.length }}</text>
          </view>
          <view
            v-if="paperFiles.length"
            class="drawer__item press"
            :class="{ 'drawer__item--active': qIndex === -1 }"
            @tap="selectQuestion(-1)"
          >
            <text class="drawer__dot" style="background: #8b96b3" />
            <text class="drawer__no">📎</text>
            <view class="drawer__body">
              <text class="drawer__type">整卷附件</text>
              <text class="drawer__verdict">{{ paperFiles.length }} 个文件</text>
            </view>
          </view>
        </scroll-view>
      </view>

      <!-- 右侧附件面板 -->
      <view v-if="attachPanelOpen" class="mask" @tap="attachPanelOpen = false" />
      <view class="attach-panel" :class="{ 'attach-panel--open': attachPanelOpen }">
        <text class="drawer__title">
          {{ qIndex === -1 ? "整卷附件" : `第 ${currentQuestion?.no ?? ""} 题附件` }}
        </text>
        <scroll-view class="drawer__scroll" scroll-y>
          <view class="attach-panel__thumbs">
            <view
              v-for="file in attachTarget.filter((f) => f.is_image)"
              :key="file.id"
              class="attach-panel__thumb"
              @tap="previewFile(file)"
            >
              <image v-if="thumbPaths[file.id]" class="thumb__img" :src="thumbPaths[file.id]" mode="aspectFill" />
              <view v-else class="thumb__placeholder"><text>🖼️</text></view>
            </view>
          </view>
          <view class="file-list">
            <view
              v-for="file in attachTarget.filter((f) => !f.is_image)"
              :key="file.id"
              class="file-chip press"
              @tap="previewFile(file)"
            >
              <text>📄</text>
              <text class="file-chip__name">{{ file.file_name }}</text>
              <text class="file-chip__size">{{ formatSize(file.file_size) }}</text>
            </view>
          </view>
        </scroll-view>
      </view>
    </template>
  </view>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 28rpx 0;
  display: flex;
  flex-direction: column;
  gap: 22rpx;
}

.empty {
  padding: 120rpx 40rpx;
  text-align: center;
  color: #8b96b3;
  font-size: 28rpx;
}

.empty-inline {
  padding: 24rpx 0;
  text-align: center;
  color: #b0b9cf;
  font-size: 25rpx;
}

/* 顶部信息 */
.header {
  padding: 26rpx 30rpx 22rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.header__paper {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.header__title {
  font-size: 30rpx;
  font-weight: 700;
  color: #1b2540;
}

.header__meta {
  font-size: 22rpx;
  color: #8b96b3;
}

.header__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.header__who {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  min-width: 0;
}

.header__name {
  font-size: 34rpx;
  font-weight: 700;
  color: #1b2540;
}

.header__sid {
  font-size: 22rpx;
  color: #8b96b3;
}

.header__score-box {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2rpx;
}

.header__score {
  font-size: 60rpx;
  font-weight: 800;
  color: #2f5ee0;
  line-height: 1.05;
}

.header__score--none {
  color: #b0b9cf;
}

.header__status {
  font-size: 22rpx;
  color: #8b96b3;
}

.header__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
}

.chip {
  font-size: 22rpx;
  color: #66718f;
  background: rgba(120, 140, 200, 0.12);
  border-radius: 999rpx;
  padding: 6rpx 20rpx;
}

.chip--late {
  color: #d05a1f;
  background: rgba(224, 102, 47, 0.13);
  font-weight: 600;
}

/* 卡片 */
.card {
  padding: 28rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 18rpx;
}

.card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card__title {
  font-size: 26rpx;
  font-weight: 700;
  color: #66718f;
  letter-spacing: 2rpx;
}

.card__action {
  font-size: 26rpx;
  font-weight: 600;
  color: #2f5ee0;
  padding: 4rpx 10rpx;
}

/* 题目 */
.q-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12rpx;
}

.q-head__left {
  display: flex;
  align-items: baseline;
  gap: 14rpx;
}

.q-head__no {
  font-size: 30rpx;
  font-weight: 700;
  color: #1b2540;
}

.q-head__type {
  font-size: 22rpx;
  color: #8b96b3;
}

.verdict-chip {
  font-size: 22rpx;
  font-weight: 600;
  border: 1rpx solid;
  border-radius: 999rpx;
  padding: 4rpx 18rpx;
  flex-shrink: 0;
}

.q-text {
  font-size: 28rpx;
  color: #1b2540;
  line-height: 1.7;
  white-space: pre-wrap;
}

.q-options {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.q-option {
  font-size: 26rpx;
  color: #334155;
  line-height: 1.6;
  background: rgba(255, 255, 255, 0.55);
  border-radius: 14rpx;
  padding: 14rpx 20rpx;
}

.answer-block {
  border-radius: 18rpx;
  padding: 20rpx 24rpx;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.answer-block--student {
  background: rgba(30, 158, 106, 0.08);
  border: 1rpx solid rgba(30, 158, 106, 0.18);
}

.answer-block--standard {
  background: rgba(47, 94, 224, 0.07);
  border: 1rpx solid rgba(47, 94, 224, 0.16);
}

.answer-block__label {
  font-size: 21rpx;
  font-weight: 700;
  color: #66718f;
  letter-spacing: 2rpx;
}

.answer-block__text {
  font-size: 27rpx;
  color: #1b2540;
  line-height: 1.65;
  white-space: pre-wrap;
}

.answer-block__text--empty {
  color: #b0b9cf;
}

.attach-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 76rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.65);
  border: 1rpx solid rgba(120, 140, 200, 0.25);
  font-size: 26rpx;
  font-weight: 600;
  color: #2f3d5e;
}

.q-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 4rpx;
}

.q-nav__btn {
  font-size: 25rpx;
  font-weight: 600;
  color: #2f5ee0;
  padding: 10rpx 16rpx;
}

.q-nav__btn--disabled {
  opacity: 0.3;
}

.q-nav__pos {
  font-size: 23rpx;
  color: #8b96b3;
}

/* 附件通用 */
.thumb-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 14rpx;
}

.thumb {
  width: 156rpx;
  height: 156rpx;
  border-radius: 18rpx;
  overflow: hidden;
  background: rgba(120, 140, 200, 0.1);
}

.thumb__img {
  width: 100%;
  height: 100%;
}

.thumb__placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40rpx;
  opacity: 0.5;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.file-chip {
  display: flex;
  align-items: center;
  gap: 12rpx;
  background: rgba(255, 255, 255, 0.55);
  border-radius: 16rpx;
  padding: 18rpx 22rpx;
  font-size: 25rpx;
  color: #334155;
}

.file-chip__name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-chip__size {
  font-size: 22rpx;
  color: #8b96b3;
  flex-shrink: 0;
}

/* 评分 */
.score-row {
  display: flex;
  flex-direction: column;
  gap: 18rpx;
}

.score-input {
  border: 2rpx solid rgba(120, 140, 200, 0.28);
  background: rgba(255, 255, 255, 0.7);
  border-radius: 20rpx;
  padding: 22rpx 28rpx;
  font-size: 40rpx;
  font-weight: 700;
  color: #1b2540;
  text-align: center;
}

.quick-scores {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
}

.quick-score {
  flex: 1;
  min-width: 88rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 68rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.6);
  border: 1rpx solid rgba(255, 255, 255, 0.75);
  font-size: 26rpx;
  font-weight: 600;
  color: #66718f;
}

.quick-score--active {
  background: linear-gradient(135deg, #5b8cff 0%, #4a7dff 100%);
  color: #ffffff;
  box-shadow: 0 8rpx 20rpx rgba(74, 125, 255, 0.3);
}

/* 评语 */
.feedback {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.fb {
  font-size: 26rpx;
  color: #334155;
  line-height: 1.7;
}

.fb--h2 {
  font-size: 29rpx;
  font-weight: 700;
  color: #1b2540;
  margin-top: 10rpx;
}

.fb--h3 {
  font-size: 27rpx;
  font-weight: 650;
  color: #2f3d5e;
  margin-top: 6rpx;
}

.fb--li {
  padding-left: 8rpx;
}

.fb--strong {
  font-weight: 700;
  color: #1b2540;
}

.feedback-raw {
  font-size: 26rpx;
  color: #334155;
  line-height: 1.7;
  white-space: pre-wrap;
}

.feedback-empty {
  font-size: 25rpx;
  color: #b0b9cf;
}

.feedback-editor {
  width: 100%;
  box-sizing: border-box;
  border: 2rpx solid rgba(120, 140, 200, 0.28);
  background: rgba(255, 255, 255, 0.7);
  border-radius: 20rpx;
  padding: 22rpx 26rpx;
  font-size: 26rpx;
  line-height: 1.7;
  min-height: 220rpx;
}

/* 底部操作栏 */
.bottom-spacer {
  height: calc(150rpx + env(safe-area-inset-bottom));
}

.action-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 18rpx 28rpx calc(env(safe-area-inset-bottom) + 18rpx);
  display: flex;
  align-items: center;
  gap: 16rpx;
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(24px) saturate(1.4);
  -webkit-backdrop-filter: blur(24px) saturate(1.4);
  border-top: 1rpx solid rgba(255, 255, 255, 0.9);
  box-shadow: 0 -8rpx 30rpx rgba(80, 100, 180, 0.1);
  z-index: 20;
}

.nav-btn {
  width: 88rpx;
  height: 88rpx;
  border-radius: 999rpx;
  background: rgba(120, 140, 200, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 44rpx;
  color: #2f3d5e;
  flex-shrink: 0;
}

.nav-btn--disabled {
  opacity: 0.3;
}

.save-btn {
  flex: 1;
  min-height: 88rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28rpx;
  font-weight: 650;
  margin: 0;
  border-radius: 999rpx;
}

.save-btn--plain {
  flex: 0 0 160rpx;
  background: rgba(255, 255, 255, 0.85);
  border: 2rpx solid rgba(120, 140, 200, 0.3);
  color: #2f3d5e;
}

/* 题目抽屉 */
.drawer-fab {
  position: fixed;
  left: 0;
  top: 42%;
  z-index: 30;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2rpx;
  padding: 18rpx 12rpx;
  border-radius: 0 20rpx 20rpx 0;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(16px);
  border: 1rpx solid rgba(120, 140, 200, 0.22);
  border-left: none;
  box-shadow: 4rpx 6rpx 20rpx rgba(80, 100, 180, 0.16);
  font-size: 26rpx;
  color: #2f3d5e;
}

.drawer-fab__label {
  font-size: 20rpx;
  color: #66718f;
}

.mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  z-index: 40;
}

.drawer {
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  width: 480rpx;
  z-index: 50;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(24px);
  box-shadow: 8rpx 0 40rpx rgba(15, 23, 42, 0.15);
  transform: translateX(-100%);
  transition: transform 0.24s ease;
  display: flex;
  flex-direction: column;
  padding: calc(env(safe-area-inset-top) + 30rpx) 0 env(safe-area-inset-bottom);
}

.drawer--open {
  transform: translateX(0);
}

.drawer__title {
  font-size: 28rpx;
  font-weight: 700;
  color: #1b2540;
  padding: 0 30rpx 20rpx;
}

.drawer__scroll {
  flex: 1;
  min-height: 0;
}

.drawer__item {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 22rpx 30rpx;
}

.drawer__item--active {
  background: rgba(74, 125, 255, 0.1);
}

.drawer__dot {
  width: 16rpx;
  height: 16rpx;
  border-radius: 999rpx;
  flex-shrink: 0;
}

.drawer__no {
  font-size: 28rpx;
  font-weight: 700;
  color: #1b2540;
  width: 52rpx;
  flex-shrink: 0;
}

.drawer__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2rpx;
}

.drawer__type {
  font-size: 24rpx;
  color: #334155;
}

.drawer__verdict {
  font-size: 21rpx;
  color: #8b96b3;
}

.drawer__attach {
  font-size: 22rpx;
  color: #66718f;
  flex-shrink: 0;
}

/* 附件右侧面板 */
.attach-panel {
  position: fixed;
  right: 0;
  top: 0;
  bottom: 0;
  width: 520rpx;
  z-index: 50;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(24px);
  box-shadow: -8rpx 0 40rpx rgba(15, 23, 42, 0.15);
  transform: translateX(100%);
  transition: transform 0.24s ease;
  display: flex;
  flex-direction: column;
  padding: calc(env(safe-area-inset-top) + 30rpx) 24rpx env(safe-area-inset-bottom);
}

.attach-panel--open {
  transform: translateX(0);
}

.attach-panel__thumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 14rpx;
  padding-bottom: 16rpx;
}

.attach-panel__thumb {
  width: 220rpx;
  height: 220rpx;
  border-radius: 18rpx;
  overflow: hidden;
  background: rgba(120, 140, 200, 0.1);
}
</style>
