<script setup lang="ts">
/**
 * 作业考试列表：大卡片 + 进行中/已完成分段。
 * 数据源 /api/mp/tasks（todo_service 同源）。教师端视图在 P3 接入。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";
import { useAuthStore } from "../../stores/auth";

interface TaskItem {
  id: string;
  source_type: string;
  source_id: number | string;
  is_exam: boolean;
  title: string;
  subtitle: string;
  status_label: string;
  tone: string;
  is_completed: boolean;
  no_deadline: boolean;
  deadline_label: string;
  relative_due_label: string;
  course_name: string;
  teacher_name: string;
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

const auth = useAuthStore();
const segment = ref<"pending" | "completed">("pending");
const pendingTasks = ref<TaskItem[]>([]);
const completedTasks = ref<TaskItem[]>([]);
const teacherTasks = ref<TeacherTask[]>([]);
const loading = ref(false);
const failed = ref(false);

const visibleTasks = computed(() =>
  segment.value === "pending" ? pendingTasks.value : completedTasks.value,
);

async function loadTasks(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    if (auth.isTeacher) {
      const data = await request<{ tasks: TeacherTask[] }>({ path: "/api/mp/teacher/tasks" });
      teacherTasks.value = data.tasks;
      return;
    }
    const data = await request<{ pending: TaskItem[]; completed: TaskItem[] }>({
      path: "/api/mp/tasks",
    });
    pendingTasks.value = data.pending;
    completedTasks.value = data.completed;
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

function openTeacherTask(task: TeacherTask): void {
  uni.navigateTo({ url: `/pages/teacher-task/index?id=${task.id}` });
}

function openTask(task: TaskItem): void {
  if (task.source_type === "assignment") {
    uni.navigateTo({ url: `/pages/task-detail/index?id=${task.source_id}` });
    return;
  }
  // 破境试炼/教务考试暂在网页端进行。
  uni.showToast({
    title: "该类型任务请在网页端完成",
    icon: "none",
  });
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
    <template v-if="auth.isTeacher">
      <view v-if="loading && !teacherTasks.length" class="empty"><text>加载中…</text></view>
      <view v-else-if="failed" class="empty" @tap="loadTasks"><text>加载失败，点击重试</text></view>
      <view v-else-if="!teacherTasks.length" class="empty"><text>暂无绑定课堂的作业/考试</text></view>

      <view
        v-for="task in teacherTasks"
        :key="task.id"
        class="task-card"
        @tap="openTeacherTask(task)"
      >
        <view class="task-card__top">
          <text class="task-card__badge" :class="task.is_exam ? 'task-card__badge--exam' : ''">
            {{ task.is_exam ? "考试" : "作业" }}
          </text>
          <text class="task-card__status">{{ task.status_label }}</text>
        </view>
        <text class="task-card__title">{{ task.title }}</text>
        <text class="task-card__course">{{ task.course_name }} · {{ task.class_name }}</text>
        <view class="teacher-progress">
          <view class="teacher-progress__bar">
            <view
              class="teacher-progress__fill"
              :style="{ width: `${task.student_total ? Math.round((task.submitted_count / task.student_total) * 100) : 0}%` }"
            />
          </view>
          <text class="teacher-progress__text">已交 {{ task.submitted_count }}/{{ task.student_total }}</text>
          <text v-if="task.pending_grade_count" class="teacher-progress__pending">
            待批 {{ task.pending_grade_count }}
          </text>
        </view>
      </view>
    </template>

    <template v-else>
      <view class="segment">
        <view
          class="segment__item"
          :class="{ 'segment__item--active': segment === 'pending' }"
          @tap="segment = 'pending'"
        >
          <text>进行中 {{ pendingTasks.length }}</text>
        </view>
        <view
          class="segment__item"
          :class="{ 'segment__item--active': segment === 'completed' }"
          @tap="segment = 'completed'"
        >
          <text>已完成 {{ completedTasks.length }}</text>
        </view>
      </view>

      <view v-if="loading && !visibleTasks.length" class="empty">
        <text>加载中…</text>
      </view>
      <view v-else-if="failed" class="empty" @tap="loadTasks">
        <text>加载失败，点击重试</text>
      </view>
      <view v-else-if="!visibleTasks.length" class="empty">
        <text>{{ segment === "pending" ? "🎉 没有进行中的任务" : "还没有已完成的任务" }}</text>
      </view>

      <view
        v-for="task in visibleTasks"
        :key="task.id"
        class="task-card"
        @tap="openTask(task)"
      >
        <view class="task-card__top">
          <text class="task-card__badge" :class="task.is_exam ? 'task-card__badge--exam' : ''">
            {{ task.is_exam ? "考试" : "作业" }}
          </text>
          <text v-if="task.status_label" class="task-card__status">{{ task.status_label }}</text>
        </view>
        <text class="task-card__title">{{ task.title }}</text>
        <text class="task-card__course">{{ task.course_name }} · {{ task.teacher_name }}</text>
        <view class="task-card__bottom">
          <text class="task-card__deadline">{{ task.deadline_label }}</text>
          <text
            v-if="!task.is_completed"
            class="task-card__relative"
          >{{ task.relative_due_label }}</text>
        </view>
      </view>
    </template>
  </view>
</template>

<style scoped>
.tasks {
  min-height: 100vh;
  padding: 32rpx 32rpx calc(env(safe-area-inset-bottom) + 32rpx);
  background: #f4f6fb;
  display: flex;
  flex-direction: column;
  gap: 28rpx;
}

.teacher-progress {
  display: flex;
  align-items: center;
  gap: 16rpx;
  margin-top: 8rpx;
}

.teacher-progress__bar {
  flex: 1;
  height: 12rpx;
  border-radius: 999rpx;
  background: #eef2f7;
  overflow: hidden;
}

.teacher-progress__fill {
  height: 100%;
  border-radius: 999rpx;
  background: #4a7dff;
}

.teacher-progress__text {
  font-size: 24rpx;
  color: #64748b;
}

.teacher-progress__pending {
  font-size: 24rpx;
  font-weight: 700;
  color: #e0662f;
}

.segment {
  display: flex;
  background: #e8ecf5;
  border-radius: 24rpx;
  padding: 8rpx;
}

.segment__item {
  flex: 1;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 76rpx;
  border-radius: 18rpx;
  font-size: 28rpx;
  color: #64748b;
}

.segment__item--active {
  background: #ffffff;
  color: #16213a;
  font-weight: 600;
  box-shadow: 0 4rpx 12rpx rgba(15, 23, 42, 0.08);
}

.empty {
  padding: 96rpx 0;
  text-align: center;
  color: #94a3b8;
  font-size: 28rpx;
}

.task-card {
  background: #ffffff;
  border-radius: 32rpx;
  padding: 36rpx 36rpx 32rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
  box-shadow: 0 8rpx 32rpx rgba(15, 23, 42, 0.05);
}

.task-card__top {
  display: flex;
  align-items: center;
  gap: 16rpx;
}

.task-card__badge {
  font-size: 22rpx;
  color: #4a7dff;
  background: rgba(74, 125, 255, 0.12);
  border-radius: 10rpx;
  padding: 4rpx 14rpx;
}

.task-card__badge--exam {
  color: #e0662f;
  background: rgba(224, 102, 47, 0.12);
}

.task-card__status {
  font-size: 22rpx;
  color: #94a3b8;
}

.task-card__title {
  font-size: 32rpx;
  font-weight: 600;
  color: #16213a;
  line-height: 1.5;
}

.task-card__course {
  font-size: 24rpx;
  color: #94a3b8;
}

.task-card__bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 6rpx;
}

.task-card__deadline {
  font-size: 24rpx;
  color: #64748b;
}

.task-card__relative {
  font-size: 26rpx;
  font-weight: 600;
  color: #e0662f;
}
</style>
