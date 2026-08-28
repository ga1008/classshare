<script setup lang="ts">
/**
 * 作业考试：学生三分段（进行中/已完成/已截止）大卡列表 + 教师进度列表。
 * 数据源 /api/mp/tasks（assignments 直查单一真源，与首页统计对齐）。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";
import { formatDueLabel, relativeDueLabel } from "../../utils/format";
import { useAuthStore } from "../../stores/auth";

interface TaskItem {
  source_type: string;
  source_id: number;
  is_exam: boolean;
  title: string;
  course_name: string;
  teacher_name: string;
  due_at: string;
  no_deadline: boolean;
  is_accepting: boolean;
  status_label: string;
  score: number | null;
}

interface TeacherTask {
  id: number;
  title: string;
  status: string;
  status_label: string;
  is_exam: boolean;
  due_at: string;
  course_name: string;
  class_name: string;
  student_total: number;
  submitted_count: number;
  graded_count: number;
  pending_grade_count: number;
}

type Segment = "pending" | "completed" | "expired";

const SEGMENTS: Array<{ key: Segment; label: string }> = [
  { key: "pending", label: "进行中" },
  { key: "completed", label: "已完成" },
  { key: "expired", label: "已截止" },
];

const EMPTY_COPY: Record<Segment, string> = {
  pending: "🎉 没有进行中的任务",
  completed: "还没有完成的任务",
  expired: "没有错过的任务，很棒",
};

const auth = useAuthStore();
const segment = ref<Segment>("pending");
const buckets = ref<Record<Segment, TaskItem[]>>({ pending: [], completed: [], expired: [] });
const teacherTasks = ref<TeacherTask[]>([]);
const loading = ref(false);
const failed = ref(false);

const visibleTasks = computed(() => buckets.value[segment.value] ?? []);

async function loadTasks(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    if (auth.isTeacher) {
      const data = await request<{ tasks: TeacherTask[] }>({ path: "/api/mp/teacher/tasks" });
      teacherTasks.value = data.tasks;
      return;
    }
    buckets.value = await request<Record<Segment, TaskItem[]>>({ path: "/api/mp/tasks" });
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

function openTask(task: TaskItem): void {
  uni.navigateTo({ url: `/pages/task-detail/index?id=${task.source_id}` });
}

function openTeacherTask(task: TeacherTask): void {
  uni.navigateTo({ url: `/pages/teacher-task/index?id=${task.id}` });
}

onShow(() => {
  void loadTasks();
});

onPullDownRefresh(() => {
  void loadTasks();
});
</script>

<template>
  <view class="tasks">
    <!-- 教师视图 -->
    <template v-if="auth.isTeacher">
      <view v-if="loading && !teacherTasks.length" class="empty"><text>加载中…</text></view>
      <view v-else-if="failed" class="empty" @tap="loadTasks"><text>加载失败，点击重试</text></view>
      <view v-else-if="!teacherTasks.length" class="empty"><text>暂无绑定课堂的作业/考试</text></view>

      <view
        v-for="task in teacherTasks"
        :key="task.id"
        class="glass-card press task-card"
        @tap="openTeacherTask(task)"
      >
        <view class="task-card__top">
          <text class="badge" :class="task.is_exam ? 'badge--exam' : 'badge--hw'">
            {{ task.is_exam ? "考试" : "作业" }}
          </text>
          <text class="task-card__status">{{ task.status_label }}</text>
        </view>
        <text class="task-card__title">{{ task.title }}</text>
        <text class="task-card__meta">{{ task.course_name }} · {{ task.class_name }}</text>
        <view class="progress-row">
          <view class="progress-bar">
            <view
              class="progress-bar__fill"
              :style="{ width: `${task.student_total ? Math.round((task.submitted_count / task.student_total) * 100) : 0}%` }"
            />
          </view>
          <text class="progress-row__text">已交 {{ task.submitted_count }}/{{ task.student_total }}</text>
          <text v-if="task.pending_grade_count" class="progress-row__pending">
            待批 {{ task.pending_grade_count }}
          </text>
        </view>
      </view>
    </template>

    <!-- 学生视图 -->
    <template v-else>
      <view class="segment glass-chip">
        <view
          v-for="item in SEGMENTS"
          :key="item.key"
          class="segment__item"
          :class="{ 'segment__item--active': segment === item.key }"
          @tap="segment = item.key"
        >
          <text>{{ item.label }} {{ buckets[item.key]?.length ?? 0 }}</text>
        </view>
      </view>

      <view v-if="loading && !visibleTasks.length" class="empty"><text>加载中…</text></view>
      <view v-else-if="failed" class="empty" @tap="loadTasks"><text>加载失败，点击重试</text></view>
      <view v-else-if="!visibleTasks.length" class="empty">
        <text>{{ EMPTY_COPY[segment] }}</text>
      </view>

      <view
        v-for="task in visibleTasks"
        :key="task.source_id"
        class="glass-card press task-card"
        @tap="openTask(task)"
      >
        <view class="task-card__top">
          <text class="badge" :class="task.is_exam ? 'badge--exam' : 'badge--hw'">
            {{ task.is_exam ? "考试" : "作业" }}
          </text>
          <text
            v-if="segment === 'completed' && task.score !== null && task.score !== undefined"
            class="task-card__score"
          >{{ task.score }} 分</text>
          <text v-else class="task-card__status">{{ task.status_label }}</text>
        </view>
        <text class="task-card__title">{{ task.title }}</text>
        <text class="task-card__meta">{{ task.course_name }} · {{ task.teacher_name }}</text>
        <view class="task-card__bottom">
          <text class="task-card__due">{{ formatDueLabel(task.due_at) }}</text>
          <text
            v-if="segment === 'pending' && !task.no_deadline"
            class="task-card__relative"
          >{{ relativeDueLabel(task.due_at) }}</text>
        </view>
      </view>
    </template>
  </view>
</template>

<style scoped>
.tasks {
  min-height: 100vh;
  padding: 28rpx 30rpx calc(env(safe-area-inset-bottom) + 32rpx);
  display: flex;
  flex-direction: column;
  gap: 26rpx;
}

.empty {
  padding: 110rpx 40rpx;
  text-align: center;
  color: #8b96b3;
  font-size: 28rpx;
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
  min-height: 76rpx;
  border-radius: 999rpx;
  font-size: 27rpx;
  color: #66718f;
  transition: all 160ms ease;
}

.segment__item--active {
  background: rgba(255, 255, 255, 0.95);
  color: #1b2540;
  font-weight: 600;
  box-shadow: 0 6rpx 18rpx rgba(80, 100, 180, 0.14);
}

.task-card {
  padding: 34rpx 36rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.task-card__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.badge {
  font-size: 22rpx;
  font-weight: 600;
  border-radius: 12rpx;
  padding: 6rpx 16rpx;
}

.badge--hw {
  color: #2f5ee0;
  background: rgba(74, 125, 255, 0.14);
}

.badge--exam {
  color: #d05a1f;
  background: rgba(224, 102, 47, 0.14);
}

.task-card__status {
  font-size: 23rpx;
  color: #8b96b3;
}

.task-card__score {
  font-size: 30rpx;
  font-weight: 800;
  color: #1b2540;
}

.task-card__title {
  font-size: 32rpx;
  font-weight: 650;
  color: #1b2540;
  line-height: 1.5;
}

.task-card__meta {
  font-size: 24rpx;
  color: #8b96b3;
}

.task-card__bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 4rpx;
}

.task-card__due {
  font-size: 24rpx;
  color: #66718f;
}

.task-card__relative {
  font-size: 25rpx;
  font-weight: 700;
  color: #d05a1f;
}

.progress-row {
  display: flex;
  align-items: center;
  gap: 16rpx;
  margin-top: 6rpx;
}

.progress-bar {
  flex: 1;
  height: 12rpx;
  border-radius: 999rpx;
  background: rgba(120, 140, 200, 0.16);
  overflow: hidden;
}

.progress-bar__fill {
  height: 100%;
  border-radius: 999rpx;
  background: linear-gradient(90deg, #5b8cff, #4a7dff);
}

.progress-row__text {
  font-size: 24rpx;
  color: #66718f;
}

.progress-row__pending {
  font-size: 24rpx;
  font-weight: 700;
  color: #d05a1f;
}
</style>
