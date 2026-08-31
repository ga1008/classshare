<script setup lang="ts">
/**
 * 教师端提交进度总览。
 *
 * 数据源 GET /api/mp/teacher/assignment/{id}/grading（服务端聚合，
 * 与批阅页共用）；批量动作复用既有 Web API：
 * - POST /api/assignments/{id}/submissions/batch-grade  AI 批量批改
 * - POST /api/assignments/{id}/submissions/zero-unsubmitted 缺交记零
 * 单份批阅在独立的 teacher-grade 页完成。
 */
import { onLoad, onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";

interface GradeEntry {
  submission_id: number | null;
  student_pk_id: number;
  student_name: string;
  student_id_number: string;
  status: string;
  status_label: string;
  score: number | null;
  is_late: boolean;
  is_absence_zero: boolean;
  files: Array<{ id: number }>;
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

const assignmentId = ref("");
const data = ref<GradingResponse | null>(null);
const loading = ref(true);
const failed = ref(false);
const segment = ref<"submitted" | "unsubmitted">("submitted");
const batchRunning = ref(false);
const needsRefresh = ref(false);

const submittedEntries = computed(
  () => (data.value?.entries ?? []).filter((entry) => entry.status !== "unsubmitted"),
);
const unsubmittedEntries = computed(
  () => (data.value?.entries ?? []).filter((entry) => entry.status === "unsubmitted"),
);
const visibleEntries = computed(() =>
  segment.value === "submitted" ? submittedEntries.value : unsubmittedEntries.value,
);

async function loadData(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    data.value = await request<GradingResponse>({
      path: `/api/mp/teacher/assignment/${assignmentId.value}/grading`,
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

function openGrade(entry: GradeEntry): void {
  if (!entry.submission_id) return;
  needsRefresh.value = true;
  uni.navigateTo({
    url: `/pages/teacher-grade/index?id=${assignmentId.value}&sid=${entry.submission_id}`,
  });
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

const nudging = ref(false);

async function nudgeUnsubmitted(): Promise<void> {
  if (nudging.value) return;
  const count = unsubmittedEntries.value.length;
  if (!count) {
    uni.showToast({ title: "没有未提交的学生", icon: "none" });
    return;
  }
  const confirmed = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: "一键催交",
      content: `给 ${count} 名未提交学生发送微信催交提醒？（仅送达已在小程序允许通知的学生，每人每天最多一次）`,
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
  if (!confirmed) return;
  nudging.value = true;
  try {
    const data = await request<{ pushed: number; no_grant: number; total_unsubmitted: number }>({
      path: `/api/mp/teacher/assignment/${assignmentId.value}/nudge`,
      method: "POST",
      data: {},
    });
    const skipped = data.total_unsubmitted - data.pushed;
    uni.showModal({
      title: "催交完成",
      content: `已推送 ${data.pushed} 人${skipped > 0 ? `；${skipped} 人未订阅通知或今日已提醒` : ""}。`,
      showCancel: false,
    });
  } catch (error: unknown) {
    uni.showToast({
      title: error instanceof Error ? error.message : "催交失败",
      icon: "none",
    });
  } finally {
    nudging.value = false;
  }
}

async function zeroUnsubmitted(): Promise<void> {
  const count = unsubmittedEntries.value.filter((entry) => !entry.is_absence_zero).length;
  if (!count) {
    uni.showToast({ title: "没有可记零的学生", icon: "none" });
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

onShow(() => {
  // 从批阅页返回后刷新计数与分数
  if (needsRefresh.value && data.value) {
    needsRefresh.value = false;
    void loadData();
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
        <text class="title-block__meta">
          {{ data.assignment.course_name }} · {{ data.assignment.class_name }}
        </text>
      </view>

      <!-- 统计大数字 -->
      <view class="stats glass-card">
        <view class="stat">
          <text class="stat__value">{{ data.stats.submitted_count }}<text class="stat__sub">/{{ data.stats.total_students }}</text></text>
          <text class="stat__label">已提交</text>
        </view>
        <view class="stat__divider" />
        <view class="stat">
          <text class="stat__value" :class="{ 'stat__value--warn': data.stats.pending_grade_count }">
            {{ data.stats.pending_grade_count }}
          </text>
          <text class="stat__label">待批改</text>
        </view>
        <view class="stat__divider" />
        <view class="stat">
          <text class="stat__value">{{ data.stats.graded_count }}</text>
          <text class="stat__label">已批改</text>
        </view>
        <view class="stat__divider" />
        <view class="stat">
          <text class="stat__value">{{ data.stats.average_score }}</text>
          <text class="stat__label">平均分</text>
        </view>
      </view>

      <!-- 批量操作 -->
      <view class="actions">
        <view
          class="action-btn press"
          :class="{ 'action-btn--busy': batchRunning }"
          @tap="runBatchAiGrading"
        >
          <text>🤖 AI 批改待批 {{ data.stats.pending_grade_count }} 份</text>
        </view>
        <view
          class="action-btn action-btn--secondary press"
          :class="{ 'action-btn--busy': nudging }"
          @tap="nudgeUnsubmitted"
        >
          <text>📣 催交</text>
        </view>
        <view class="action-btn action-btn--secondary press" @tap="zeroUnsubmitted">
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
        <view
          v-for="entry in visibleEntries"
          :key="entry.student_pk_id"
          class="name-chip glass-chip"
          :class="{ 'name-chip--zeroed': entry.is_absence_zero }"
        >
          <text>{{ entry.student_name }}</text>
          <text v-if="entry.is_absence_zero" class="name-chip__zero">已记零</text>
        </view>
      </view>

      <!-- 已交列表（点击进入批阅页） -->
      <template v-else>
        <view
          v-for="entry in visibleEntries"
          :key="entry.student_pk_id"
          class="sub-card glass-card press"
          @tap="openGrade(entry)"
        >
          <view class="sub-card__avatar">
            <text>{{ entry.student_name.slice(0, 1) }}</text>
          </view>
          <view class="sub-card__who">
            <text class="sub-card__name">{{ entry.student_name }}</text>
            <text class="sub-card__meta">
              {{ entry.student_id_number }}{{ entry.files.length ? ` · 附件${entry.files.length}` : "" }}{{ entry.is_late ? " · 迟交" : "" }}
            </text>
          </view>
          <view class="sub-card__right">
            <text
              v-if="entry.score !== null && entry.score !== undefined"
              class="sub-card__score"
            >{{ entry.score }}</text>
            <text
              v-else
              class="sub-card__status"
              :class="{ 'sub-card__status--pending': entry.status === 'submitted' }"
            >{{ entry.status_label }}</text>
            <text class="sub-card__arrow">›</text>
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
  gap: 22rpx;
}

.empty {
  padding: 100rpx 40rpx;
  text-align: center;
  color: #8b96b3;
  font-size: 28rpx;
}

.title-block {
  padding: 8rpx 4rpx 0;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.title-block__title {
  font-size: 36rpx;
  font-weight: 700;
  color: #1b2540;
  line-height: 1.4;
}

.title-block__meta {
  font-size: 24rpx;
  color: #8b96b3;
}

.stats {
  display: flex;
  align-items: center;
  padding: 30rpx 10rpx;
}

.stat {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
}

.stat__divider {
  width: 1rpx;
  height: 52rpx;
  background: rgba(120, 140, 200, 0.18);
}

.stat__value {
  font-size: 38rpx;
  font-weight: 800;
  color: #1b2540;
  line-height: 1.1;
}

.stat__sub {
  font-size: 24rpx;
  font-weight: 600;
  color: #8b96b3;
}

.stat__value--warn {
  color: #d05a1f;
}

.stat__label {
  font-size: 22rpx;
  color: #8b96b3;
}

.actions {
  display: flex;
  gap: 16rpx;
}

.action-btn {
  flex: 1;
  min-height: 88rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999rpx;
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
  flex: 0 0 210rpx;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
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
  border-radius: 999rpx;
  font-size: 28rpx;
  color: #66718f;
  transition: all 160ms ease;
}

.segment__item--active {
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 6rpx 18rpx rgba(80, 100, 180, 0.14);
  color: #1b2540;
  font-weight: 600;
}

.name-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16rpx;
}

.name-chip {
  padding: 16rpx 30rpx;
  font-size: 26rpx;
  color: #334155;
  display: flex;
  align-items: center;
  gap: 10rpx;
}

.name-chip--zeroed {
  opacity: 0.65;
}

.name-chip__zero {
  font-size: 20rpx;
  color: #dc2626;
}

.sub-card {
  padding: 26rpx 30rpx;
  display: flex;
  align-items: center;
  gap: 22rpx;
}

.sub-card__avatar {
  width: 76rpx;
  height: 76rpx;
  border-radius: 24rpx;
  background: linear-gradient(135deg, #6a7bff 0%, #8b5cf6 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  font-size: 32rpx;
  font-weight: 700;
  flex-shrink: 0;
}

.sub-card__who {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  min-width: 0;
}

.sub-card__name {
  font-size: 30rpx;
  font-weight: 600;
  color: #1b2540;
}

.sub-card__meta {
  font-size: 22rpx;
  color: #8b96b3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sub-card__right {
  display: flex;
  align-items: center;
  gap: 14rpx;
  flex-shrink: 0;
}

.sub-card__score {
  font-size: 40rpx;
  font-weight: 800;
  color: #1b2540;
}

.sub-card__status {
  font-size: 23rpx;
  color: #8b96b3;
}

.sub-card__status--pending {
  color: #d05a1f;
  font-weight: 600;
}

.sub-card__arrow {
  font-size: 36rpx;
  color: #b0b9cf;
  line-height: 1;
}
</style>
