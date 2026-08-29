<script setup lang="ts">
/**
 * 教师端提交进度 + 移动批阅。
 *
 * 数据与动作全部复用既有 Web API（bearer 直通）：
 * - GET  /api/assignments/{id}/submissions  统计 + 已交/未交全名单 + 答案
 * - POST /api/submissions/{id}/grade        打分（后端自动套迟交策略）
 * - POST /api/assignments/{id}/submissions/batch-grade  AI 批量批改
 * - POST /api/assignments/{id}/submissions/zero-unsubmitted 缺交记零
 */
import { onLoad, onPullDownRefresh } from "@dcloudio/uni-app";
import { computed, reactive, ref } from "vue";

import { request } from "../../utils/api";
import { previewProtectedFile } from "../../utils/preview";

interface SubmissionFile {
  id: number;
  file_name: string;
  mime_type: string;
  is_image: boolean;
}

interface SubmissionEntry {
  id: number | null;
  student_pk_id: number;
  student_name: string;
  student_id_number: string;
  status: string;
  score: number | null;
  feedback_md: string | null;
  submitted_at: string | null;
  answers_json: string | null;
  file_count: number;
  is_late_submission: number;
  is_absence_score: number;
}

interface SubmissionsResponse {
  status: string;
  stats: {
    total_students: number;
    total_submissions: number;
    unsubmitted_count: number;
    graded_count: number;
    pending_grade_count: number;
    grading_count: number;
    average_score: number;
  };
  submissions: SubmissionEntry[];
  assignment: { id: number | string; title: string; exam_paper_id?: string | null };
}

const STATUS_LABELS: Record<string, string> = {
  submitted: "待批改",
  grading: "AI批改中",
  grading_review: "待确认",
  graded: "已批改",
  unsubmitted: "未提交",
};

const assignmentId = ref("");
const data = ref<SubmissionsResponse | null>(null);
const loading = ref(true);
const failed = ref(false);
const segment = ref<"submitted" | "unsubmitted">("submitted");
const expandedId = ref<number | null>(null);
const gradeScore = ref("");
const gradeFeedback = ref("");
const saving = ref(false);
const batchRunning = ref(false);

const submittedEntries = computed(
  () => (data.value?.submissions ?? []).filter((entry) => entry.status !== "unsubmitted"),
);
const unsubmittedEntries = computed(
  () => (data.value?.submissions ?? []).filter((entry) => entry.status === "unsubmitted"),
);
const visibleEntries = computed(() =>
  segment.value === "submitted" ? submittedEntries.value : unsubmittedEntries.value,
);

function parseAnswers(entry: SubmissionEntry): Array<{ question?: string; answer?: string; attachments?: unknown[] }> {
  try {
    const parsed = JSON.parse(entry.answers_json || "{}") as { answers?: [] };
    return Array.isArray(parsed.answers) ? parsed.answers : [];
  } catch {
    return [];
  }
}

async function loadData(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    data.value = await request<SubmissionsResponse>({
      path: `/api/assignments/${assignmentId.value}/submissions`,
    });
  } catch (error: unknown) {
    failed.value = true;
    if ((error as { statusCode?: number }).statusCode === 401) {
      uni.reLaunch({ url: "/pages/welcome/index" });
    }
  } finally {
    loading.value = false;
    uni.stopPullDownRefresh();
  }
}

const filesBySubmission = reactive<Record<number, SubmissionFile[]>>({});

async function loadSubmissionFiles(submissionId: number): Promise<void> {
  if (filesBySubmission[submissionId]) return;
  try {
    const data = await request<{ files: SubmissionFile[] }>({
      path: `/api/mp/teacher/submission/${submissionId}/files`,
    });
    filesBySubmission[submissionId] = data.files;
  } catch {
    filesBySubmission[submissionId] = [];
  }
}

function previewFile(file: SubmissionFile): void {
  void previewProtectedFile({
    path: `/submissions/download/${file.id}`,
    fileName: file.file_name,
    mimeType: file.mime_type,
  });
}

function toggleGrade(entry: SubmissionEntry): void {
  if (!entry.id) return;
  if (expandedId.value === entry.id) {
    expandedId.value = null;
    return;
  }
  expandedId.value = entry.id;
  gradeScore.value = entry.score !== null && entry.score !== undefined ? String(entry.score) : "";
  gradeFeedback.value = entry.feedback_md || "";
  if (entry.file_count) {
    void loadSubmissionFiles(entry.id);
  }
}

async function saveGrade(entry: SubmissionEntry): Promise<void> {
  if (!entry.id || saving.value) return;
  const score = Number(gradeScore.value);
  if (!Number.isFinite(score) || score < 0 || score > 100) {
    uni.showToast({ title: "请输入 0-100 的分数", icon: "none" });
    return;
  }
  saving.value = true;
  try {
    await request({
      path: `/api/submissions/${entry.id}/grade`,
      method: "POST",
      data: { score, feedback_md: gradeFeedback.value },
    });
    uni.showToast({ title: "已保存", icon: "success" });
    expandedId.value = null;
    await loadData();
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

async function runBatchAiGrading(): Promise<void> {
  if (batchRunning.value) return;
  const pending = data.value?.stats.pending_grade_count ?? 0;
  if (!pending) {
    uni.showToast({ title: "没有待批改的提交", icon: "none" });
    return;
  }
  const confirmed = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: "AI 批改",
      content: `对 ${pending} 份待批改提交发起 AI 批改？结果需在批改完成后确认。`,
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
  if (!confirmed) return;
  batchRunning.value = true;
  try {
    await request({
      path: `/api/assignments/${assignmentId.value}/submissions/batch-grade`,
      method: "POST",
      data: {},
    });
    uni.showToast({ title: "AI 批改已发起", icon: "success" });
    await loadData();
  } catch (error: unknown) {
    uni.showToast({
      title: error instanceof Error ? error.message : "发起失败",
      icon: "none",
    });
  } finally {
    batchRunning.value = false;
  }
}

async function zeroUnsubmitted(): Promise<void> {
  const count = unsubmittedEntries.value.length;
  if (!count) {
    uni.showToast({ title: "没有未提交的学生", icon: "none" });
    return;
  }
  const confirmed = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: "缺交记零",
      content: `为 ${count} 名未提交学生记 0 分（占位记录，可撤销）？`,
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
  if (!confirmed) return;
  try {
    await request({
      path: `/api/assignments/${assignmentId.value}/submissions/zero-unsubmitted`,
      method: "POST",
      data: {},
    });
    uni.showToast({ title: "已记零", icon: "success" });
    await loadData();
  } catch (error: unknown) {
    uni.showToast({
      title: error instanceof Error ? error.message : "操作失败",
      icon: "none",
    });
  }
}

onLoad((query) => {
  assignmentId.value = String((query as Record<string, string>)?.id || "");
  if (assignmentId.value) {
    void loadData();
  } else {
    failed.value = true;
    loading.value = false;
  }
});

onPullDownRefresh(() => {
  void loadData();
});
</script>

<template>
  <view class="page">
    <view v-if="loading && !data" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="loadData"><text>加载失败，点击重试</text></view>

    <template v-else-if="data">
      <!-- 任务标题 -->
      <view class="title-block">
        <text class="title-block__title">{{ data.assignment.title }}</text>
      </view>

      <!-- 统计大数字 -->
      <view class="stats">
        <view class="stat glass-card">
          <text class="stat__value">{{ data.stats.total_submissions }}/{{ data.stats.total_students }}</text>
          <text class="stat__label">已提交</text>
        </view>
        <view class="stat glass-card">
          <text class="stat__value stat__value--warn">{{ data.stats.pending_grade_count }}</text>
          <text class="stat__label">待批改</text>
        </view>
        <view class="stat glass-card">
          <text class="stat__value">{{ data.stats.graded_count }}</text>
          <text class="stat__label">已批改</text>
        </view>
        <view class="stat glass-card">
          <text class="stat__value">{{ data.stats.average_score }}</text>
          <text class="stat__label">平均分</text>
        </view>
      </view>

      <!-- 批量操作 -->
      <view class="actions">
        <view class="action-btn" :class="{ 'action-btn--busy': batchRunning }" @tap="runBatchAiGrading">
          <text>🤖 AI 批改待批 {{ data.stats.pending_grade_count }} 份</text>
        </view>
        <view class="action-btn action-btn--secondary" @tap="zeroUnsubmitted">
          <text>缺交记零</text>
        </view>
      </view>

      <!-- 分段 -->
      <view class="segment glass-chip">
        <view
          class="segment__item"
          :class="{ 'segment__item--active': segment === 'submitted' }"
          @tap="segment = 'submitted'"
        >
          <text>已交 {{ submittedEntries.length }}</text>
        </view>
        <view
          class="segment__item"
          :class="{ 'segment__item--active': segment === 'unsubmitted' }"
          @tap="segment = 'unsubmitted'"
        >
          <text>未交 {{ unsubmittedEntries.length }}</text>
        </view>
      </view>

      <view v-if="!visibleEntries.length" class="empty">
        <text>{{ segment === "submitted" ? "还没有学生提交" : "🎉 全员已提交" }}</text>
      </view>

      <!-- 未交名单 -->
      <view v-if="segment === 'unsubmitted'" class="name-grid">
        <view v-for="entry in visibleEntries" :key="entry.student_pk_id" class="name-chip glass-chip">
          <text>{{ entry.student_name }}</text>
        </view>
      </view>

      <!-- 已交列表（点开批阅） -->
      <template v-else>
        <view v-for="entry in visibleEntries" :key="entry.student_pk_id" class="sub-card glass-card">
          <view class="sub-card__row" @tap="toggleGrade(entry)">
            <view class="sub-card__who">
              <text class="sub-card__name">{{ entry.student_name }}</text>
              <text class="sub-card__meta">
                {{ entry.student_id_number }}{{ entry.file_count ? ` · 附件${entry.file_count}` : "" }}{{ entry.is_late_submission ? " · 迟交" : "" }}
              </text>
            </view>
            <view class="sub-card__right">
              <text v-if="entry.score !== null && entry.score !== undefined" class="sub-card__score">
                {{ entry.score }}
              </text>
              <text
                class="sub-card__status"
                :class="{ 'sub-card__status--pending': entry.status === 'submitted' }"
              >
                {{ STATUS_LABELS[entry.status] || entry.status }}
              </text>
            </view>
          </view>

          <!-- 批阅展开区 -->
          <view v-if="expandedId === entry.id" class="grade-panel" @tap.stop>
            <view v-if="parseAnswers(entry).length" class="grade-panel__answers">
              <view v-for="(item, index) in parseAnswers(entry)" :key="index" class="answer-item">
                <text class="answer-item__q">{{ index + 1 }}. {{ item.question }}</text>
                <text class="answer-item__a">{{ item.answer || "（未作答）" }}</text>
              </view>
            </view>
            <text v-else class="grade-panel__no-answers">无文字作答</text>

            <view v-if="entry.file_count && filesBySubmission[entry.id!]?.length" class="file-list">
              <view
                v-for="file in filesBySubmission[entry.id!]"
                :key="file.id"
                class="file-chip"
                @tap.stop="previewFile(file)"
              >
                <text>{{ file.is_image ? "🖼️" : "📄" }}</text>
                <text class="file-chip__name">{{ file.file_name }}</text>
              </view>
            </view>

            <view class="grade-panel__form">
              <input
                v-model="gradeScore"
                class="grade-panel__score"
                type="digit"
                placeholder="分数 0-100"
              />
              <textarea
                v-model="gradeFeedback"
                class="grade-panel__feedback"
                placeholder="评语（可选）"
                :maxlength="-1"
                auto-height
              />
              <button class="grade-panel__save glass-btn-primary" :loading="saving" @tap="saveGrade(entry)">
                保存评分
              </button>
            </view>
          </view>
        </view>
      </template>
    </template>
  </view>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 28rpx 28rpx calc(env(safe-area-inset-bottom) + 40rpx);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.empty {
  padding: 100rpx 40rpx;
  text-align: center;
  color: #94a3b8;
  font-size: 28rpx;
}

.title-block {
  padding: 8rpx 4rpx 0;
}

.title-block__title {
  font-size: 36rpx;
  font-weight: 700;
  color: #16213a;
  line-height: 1.4;
}

.stats {
  display: flex;
  gap: 16rpx;
}

.stat {
  flex: 1;
  padding: 26rpx 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
}

.stat__value {
  font-size: 34rpx;
  font-weight: 700;
  color: #16213a;
}

.stat__value--warn {
  color: #e0662f;
}

.stat__label {
  font-size: 22rpx;
  color: #94a3b8;
}

.actions {
  display: flex;
  gap: 16rpx;
}

.action-btn {
  flex: 1;
  min-height: 84rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 22rpx;
  background: linear-gradient(135deg, #5b8cff 0%, #4a7dff 100%);
  box-shadow: 0 10rpx 26rpx rgba(74, 125, 255, 0.3);
  color: #ffffff;
  font-size: 26rpx;
  font-weight: 600;
}

.action-btn--busy {
  opacity: 0.6;
}

.action-btn--secondary {
  flex: 0 0 220rpx;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  border: 1rpx solid rgba(229, 72, 77, 0.25);
  box-shadow: none;
  color: #dc2626;
}

.segment {
  display: flex;
  padding: 8rpx;
}

.segment__item {
  flex: 1;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 72rpx;
  border-radius: 18rpx;
  font-size: 28rpx;
  color: #64748b;
}

.segment__item--active {
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 6rpx 18rpx rgba(80, 100, 180, 0.14);
  border-radius: 999rpx;
  color: #16213a;
  font-weight: 600;
}

.name-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16rpx;
}

.name-chip {
  padding: 16rpx 32rpx;
  font-size: 26rpx;
  color: #334155;
}

.sub-card {
  padding: 28rpx 32rpx;
}

.sub-card__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20rpx;
}

.sub-card__who {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.sub-card__name {
  font-size: 30rpx;
  font-weight: 600;
  color: #16213a;
}

.sub-card__meta {
  font-size: 22rpx;
  color: #94a3b8;
}

.sub-card__right {
  display: flex;
  align-items: center;
  gap: 16rpx;
}

.sub-card__score {
  font-size: 40rpx;
  font-weight: 800;
  color: #16213a;
}

.sub-card__status {
  font-size: 22rpx;
  color: #94a3b8;
}

.sub-card__status--pending {
  color: #e0662f;
  font-weight: 600;
}

.grade-panel {
  margin-top: 24rpx;
  border-top: 2rpx solid #f1f5f9;
  padding-top: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.grade-panel__answers {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  max-height: 600rpx;
  overflow-y: auto;
}

.answer-item {
  background: #f8fafc;
  border-radius: 16rpx;
  padding: 20rpx;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.answer-item__q {
  font-size: 24rpx;
  color: #64748b;
  line-height: 1.5;
}

.answer-item__a {
  font-size: 26rpx;
  color: #16213a;
  line-height: 1.6;
}

.grade-panel__no-answers {
  font-size: 24rpx;
  color: #94a3b8;
}

.file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 14rpx;
}

.file-chip {
  display: flex;
  align-items: center;
  gap: 10rpx;
  background: #f1f5f9;
  border-radius: 14rpx;
  padding: 14rpx 20rpx;
  font-size: 24rpx;
  color: #334155;
}

.file-chip__name {
  max-width: 400rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.grade-panel__form {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.grade-panel__score {
  border: 2rpx solid #e2e8f0;
  border-radius: 16rpx;
  padding: 18rpx 24rpx;
  font-size: 30rpx;
}

.grade-panel__feedback {
  width: 100%;
  box-sizing: border-box;
  border: 2rpx solid #e2e8f0;
  border-radius: 16rpx;
  padding: 20rpx 24rpx;
  font-size: 26rpx;
  line-height: 1.6;
  min-height: 120rpx;
}

.grade-panel__save {
  min-height: 80rpx;
  font-size: 28rpx;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
}
</style>
