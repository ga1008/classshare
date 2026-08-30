<script setup lang="ts">
/**
 * 教师批阅页（全屏顺序批阅）。
 *
 * 数据源：GET /api/mp/teacher/assignment/{id}/grading（服务端聚合：
 * 名单顺序、作答解析、评语 markdown → 结构块、附件清单）。
 * 打分动作复用既有 POST /api/submissions/{id}/grade。
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

interface GradeEntry {
  submission_id: number | null;
  student_pk_id: number;
  student_name: string;
  student_id_number: string;
  status: string;
  status_label: string;
  score: number | null;
  submitted_at: string;
  is_late: boolean;
  is_absence_zero: boolean;
  answers: Array<{ question: string; answer: string }>;
  feedback_md: string;
  feedback_blocks: FeedbackBlock[];
  files: GradeFile[];
}

interface GradingResponse {
  assignment: { id: number; title: string; is_exam: boolean; course_name: string; class_name: string };
  stats: {
    total_students: number;
    submitted_count: number;
    graded_count: number;
    pending_grade_count: number;
    average_score: number;
  };
  entries: GradeEntry[];
}

const QUICK_SCORES = [60, 70, 80, 85, 90, 95, 100];

const assignmentId = ref("");
const data = ref<GradingResponse | null>(null);
const loading = ref(true);
const failed = ref(false);
const index = ref(0);
const saving = ref(false);
const editingFeedback = ref(false);
const gradeScore = ref("");
const gradeFeedback = ref("");
const thumbPaths = reactive<Record<number, string>>({});

const queue = computed<GradeEntry[]>(
  () => (data.value?.entries ?? []).filter((entry) => entry.status !== "unsubmitted"),
);
const current = computed<GradeEntry | null>(() => queue.value[index.value] ?? null);
const hasPrev = computed(() => index.value > 0);
const hasNext = computed(() => index.value < queue.value.length - 1);
const isDirty = computed(() => {
  const entry = current.value;
  if (!entry) return false;
  const savedScore = entry.score !== null && entry.score !== undefined ? String(entry.score) : "";
  return gradeScore.value !== savedScore || gradeFeedback.value !== (entry.feedback_md || "");
});

function syncForm(entry: GradeEntry | null): void {
  editingFeedback.value = false;
  if (!entry) {
    gradeScore.value = "";
    gradeFeedback.value = "";
    return;
  }
  gradeScore.value = entry.score !== null && entry.score !== undefined ? String(entry.score) : "";
  gradeFeedback.value = entry.feedback_md || "";
}

watch(current, (entry) => {
  syncForm(entry);
  if (entry) void loadThumbnails(entry);
});

async function loadThumbnails(entry: GradeEntry): Promise<void> {
  const images = entry.files.filter((file) => file.is_image && !thumbPaths[file.id]);
  for (const file of images) {
    try {
      thumbPaths[file.id] = await downloadProtectedTempFile(`/submissions/download/${file.id}`);
    } catch {
      /* 缩略图失败退化为占位符，点击仍可走完整预览重试 */
    }
  }
}

async function loadData(keepIndex = false): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    const previousId = current.value?.submission_id ?? null;
    data.value = await request<GradingResponse>({
      path: `/api/mp/teacher/assignment/${assignmentId.value}/grading`,
    });
    if (keepIndex && previousId !== null) {
      const found = queue.value.findIndex((entry) => entry.submission_id === previousId);
      index.value = found >= 0 ? found : Math.min(index.value, Math.max(queue.value.length - 1, 0));
    }
    syncForm(current.value);
    if (current.value) void loadThumbnails(current.value);
  } catch (error: unknown) {
    failed.value = true;
    if ((error as { statusCode?: number }).statusCode === 401) {
      uni.reLaunch({ url: "/pages/welcome/index" });
    }
  } finally {
    loading.value = false;
  }
}

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

function applyQuickScore(score: number): void {
  gradeScore.value = String(score);
}

async function saveGrade(advance: boolean): Promise<void> {
  const entry = current.value;
  if (!entry?.submission_id || saving.value) return;
  const score = Number(gradeScore.value);
  if (!Number.isFinite(score) || score < 0 || score > 100) {
    uni.showToast({ title: "请输入 0-100 的分数", icon: "none" });
    return;
  }
  saving.value = true;
  try {
    await request({
      path: `/api/submissions/${entry.submission_id}/grade`,
      method: "POST",
      data: { score, feedback_md: gradeFeedback.value },
    });
    uni.showToast({ title: "已保存", icon: "success" });
    await loadData(true);
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
  void loadData().then(() => {
    if (targetSid) {
      const found = queue.value.findIndex((entry) => entry.submission_id === targetSid);
      if (found >= 0) index.value = found;
      syncForm(current.value);
    }
  });
});
</script>

<template>
  <view class="page">
    <view v-if="loading && !data" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="loadData()"><text>加载失败，点击重试</text></view>
    <view v-else-if="!current" class="empty"><text>还没有可批阅的提交</text></view>

    <template v-else>
      <!-- 学生头部 -->
      <view class="student glass-card">
        <view class="student__main">
          <view class="student__avatar">
            <text>{{ current.student_name.slice(0, 1) }}</text>
          </view>
          <view class="student__who">
            <text class="student__name">{{ current.student_name }}</text>
            <text class="student__meta">{{ current.student_id_number }}</text>
          </view>
          <view class="student__right">
            <text v-if="current.score !== null && current.score !== undefined" class="student__score">
              {{ current.score }}
            </text>
            <text
              class="student__status"
              :class="{ 'student__status--pending': current.status === 'submitted' }"
            >
              {{ current.status_label }}
            </text>
          </view>
        </view>
        <view class="student__chips">
          <text class="chip chip--index">{{ index + 1 }} / {{ queue.length }}</text>
          <text v-if="current.is_late" class="chip chip--late">迟交</text>
          <text v-if="current.files.length" class="chip">附件 {{ current.files.length }}</text>
        </view>
      </view>

      <!-- 作答 -->
      <view v-if="current.answers.length" class="card glass-card">
        <text class="card__title">作答内容</text>
        <view v-for="(item, i) in current.answers" :key="i" class="answer">
          <text class="answer__q">{{ i + 1 }}. {{ item.question }}</text>
          <text class="answer__a" :class="{ 'answer__a--empty': !item.answer }">
            {{ item.answer || "（未作答）" }}
          </text>
        </view>
      </view>

      <!-- 附件 -->
      <view v-if="current.files.length" class="card glass-card">
        <text class="card__title">附件 {{ current.files.length }}</text>
        <view v-if="current.files.some((f) => f.is_image)" class="thumb-grid">
          <view
            v-for="file in current.files.filter((f) => f.is_image)"
            :key="file.id"
            class="thumb"
            @tap="previewFile(file)"
          >
            <image
              v-if="thumbPaths[file.id]"
              class="thumb__img"
              :src="thumbPaths[file.id]"
              mode="aspectFill"
            />
            <view v-else class="thumb__placeholder"><text>🖼️</text></view>
          </view>
        </view>
        <view class="file-list">
          <view
            v-for="file in current.files.filter((f) => !f.is_image)"
            :key="file.id"
            class="file-chip press"
            @tap="previewFile(file)"
          >
            <text>📄</text>
            <text class="file-chip__name">{{ file.file_name }}</text>
            <text class="file-chip__size">{{ formatSize(file.file_size) }}</text>
          </view>
        </view>
      </view>

      <!-- 评分 -->
      <view class="card glass-card">
        <text class="card__title">评分</text>
        <view class="score-row">
          <input
            v-model="gradeScore"
            class="score-input"
            type="digit"
            placeholder="0-100"
          />
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
          <view v-if="current.feedback_blocks.length && gradeFeedback === current.feedback_md" class="feedback">
            <template v-for="(block, i) in current.feedback_blocks" :key="i">
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

      <!-- 底部操作栏 -->
      <view class="bottom-spacer" />
      <view class="action-bar">
        <view class="nav-btn press" :class="{ 'nav-btn--disabled': !hasPrev }" @tap="goPrev">
          <text>‹</text>
        </view>
        <button
          class="save-btn save-btn--plain"
          :disabled="saving"
          @tap="saveGrade(false)"
        >
          保存
        </button>
        <button
          class="save-btn glass-btn-primary"
          :loading="saving"
          :disabled="saving"
          @tap="saveGrade(true)"
        >
          {{ hasNext ? "保存并下一位" : "保存" }}
        </button>
        <view class="nav-btn press" :class="{ 'nav-btn--disabled': !hasNext }" @tap="goNext">
          <text>›</text>
        </view>
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

/* 学生头部 */
.student {
  padding: 28rpx 30rpx 24rpx;
  display: flex;
  flex-direction: column;
  gap: 18rpx;
}

.student__main {
  display: flex;
  align-items: center;
  gap: 22rpx;
}

.student__avatar {
  width: 84rpx;
  height: 84rpx;
  border-radius: 26rpx;
  background: linear-gradient(135deg, #6a7bff 0%, #8b5cf6 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  font-size: 36rpx;
  font-weight: 700;
  flex-shrink: 0;
}

.student__who {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  min-width: 0;
}

.student__name {
  font-size: 34rpx;
  font-weight: 700;
  color: #1b2540;
}

.student__meta {
  font-size: 23rpx;
  color: #8b96b3;
}

.student__right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4rpx;
}

.student__score {
  font-size: 48rpx;
  font-weight: 800;
  color: #1b2540;
  line-height: 1.1;
}

.student__status {
  font-size: 22rpx;
  color: #8b96b3;
}

.student__status--pending {
  color: #d05a1f;
  font-weight: 600;
}

.student__chips {
  display: flex;
  gap: 12rpx;
}

.chip {
  font-size: 22rpx;
  color: #66718f;
  background: rgba(120, 140, 200, 0.12);
  border-radius: 999rpx;
  padding: 6rpx 20rpx;
}

.chip--index {
  color: #2f5ee0;
  background: rgba(74, 125, 255, 0.13);
  font-weight: 600;
}

.chip--late {
  color: #d05a1f;
  background: rgba(224, 102, 47, 0.13);
  font-weight: 600;
}

/* 通用卡片 */
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

/* 作答 */
.answer {
  background: rgba(255, 255, 255, 0.55);
  border-radius: 20rpx;
  padding: 20rpx 24rpx;
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.answer__q {
  font-size: 24rpx;
  color: #66718f;
  line-height: 1.55;
}

.answer__a {
  font-size: 27rpx;
  color: #1b2540;
  line-height: 1.65;
}

.answer__a--empty {
  color: #b0b9cf;
}

/* 附件 */
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
</style>
