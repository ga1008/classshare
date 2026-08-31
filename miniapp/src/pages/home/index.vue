<script setup lang="ts">
/**
 * 首页 = "今天"：问候 + 日期 + 大议程玻璃卡 + 彩色统计块。
 * 数据源 /api/mp/home（学生的待完成/已提交与任务列表同源对齐）。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";
import { useAuthStore } from "../../stores/auth";
import { prefetchSubscribeConfig } from "../../utils/subscribe";
import { applyRoleTabs } from "../../utils/tabs";

interface AgendaEvent {
  kind: string;
  title: string;
  subtitle: string;
  hour_label: string;
  relative_label: string;
  /** completed=已过期 / current=今天 / upcoming=未来（dashboard_agenda_events 自带） */
  status?: string;
  href?: string;
  /** 手动私人待办（可编辑），来自 _attach_manual */
  is_manual?: boolean;
  todo_id?: number;
  notes?: string;
  due_at_raw?: string;
  reminder_enabled?: boolean;
}

interface TodoDraft {
  id: number;
  title: string;
  notes: string;
  date: string;
  time: string;
  reminder: boolean;
}

interface HomeData {
  role: "student" | "teacher";
  user: { id: number; name: string };
  stats: Array<{ label: string; value: string | number; note: string }>;
  agenda: AgendaEvent[];
}

const KIND_ICONS: Record<string, string> = {
  exam: "📝",
  assignment: "📚",
  class: "🏫",
  todo: "📌",
  invigilation: "👀",
};

const STAT_TONES = ["tone-blue", "tone-orange", "tone-green", "tone-purple"];

const auth = useAuthStore();
const home = ref<HomeData | null>(null);
const greeting = ref("");
const loading = ref(false);
const failed = ref(false);
const unreadTotal = ref(0);

async function loadUnread(): Promise<void> {
  try {
    const data = await request<{ summary?: { unread_total?: number } }>({
      path: "/api/message-center/summary",
    });
    unreadTotal.value = Number(data.summary?.unread_total || 0);
  } catch {
    /* 未读数是锦上添花，失败静默 */
  }
}

function openMessages(): void {
  uni.navigateTo({ url: "/pages/messages/index" });
}

/** 议程只默认展示今天与未来；过期项收进"历史"折叠区，别糊满首页。 */
const showHistory = ref(false);
const activeAgenda = computed(() =>
  (home.value?.agenda ?? []).filter((event) => event.status !== "completed").slice(0, 8),
);
const pastAgenda = computed(() =>
  (home.value?.agenda ?? []).filter((event) => event.status === "completed"),
);

const dateLine = computed(() => {
  const now = new Date();
  const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  return `${now.getMonth() + 1}月${now.getDate()}日 · ${weekdays[now.getDay()]}`;
});

async function loadGreeting(): Promise<void> {
  try {
    const data = await request<{ greeting?: { greeting_text?: string } | null }>({
      path: "/api/learning/personal-greeting",
    });
    const text = data?.greeting?.greeting_text;
    if (text) greeting.value = text;
  } catch {
    /* 欢迎语失败保持默认 */
  }
}

async function loadHome(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    home.value = await request<HomeData>({ path: "/api/mp/home" });
    applyRoleTabs(home.value.role);
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

function openAgenda(event: AgendaEvent): void {
  // 私人待办 → 编辑弹层（可改内容与提醒时间，重新加入提醒）
  if (event.is_manual && event.todo_id) {
    openTodoEditor(event);
    return;
  }
  // 作业/考试类议程直达作答页；href 形如 /assignment/12 或 /exam/take/12
  const match = /\/(?:assignment|exam\/take)\/(\d+)/.exec(event.href || "");
  if (match) {
    uni.navigateTo({ url: `/pages/task-detail/index?id=${match[1]}` });
  }
  // 其余事件（上课/监考等）小程序暂无对应页面，不再盲跳任务列表
}

// ---------- 私人待办编辑 ----------

const editingTodo = ref<TodoDraft | null>(null);
const todoSaving = ref(false);

function openTodoEditor(event: AgendaEvent): void {
  const raw = String(event.due_at_raw || "").replace("T", " ");
  const date = /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : "";
  const timeMatch = /(\d{2}:\d{2})/.exec(raw.slice(10));
  editingTodo.value = {
    id: Number(event.todo_id),
    title: event.title || "",
    notes: event.notes || "",
    date,
    time: timeMatch ? timeMatch[1] : "09:00",
    reminder: event.reminder_enabled !== false,
  };
}

async function submitTodo(extra: Record<string, unknown> = {}): Promise<void> {
  const draft = editingTodo.value;
  if (!draft || todoSaving.value) return;
  if (!draft.title.trim()) {
    uni.showToast({ title: "标题不能为空", icon: "none" });
    return;
  }
  todoSaving.value = true;
  try {
    const data: Record<string, unknown> = {
      title: draft.title.trim(),
      notes: draft.notes,
      reminder_enabled: draft.reminder,
      ...extra,
    };
    if (draft.date) {
      data.due_at = `${draft.date}T${draft.time || "09:00"}`;
    }
    await request({ path: `/api/mp/todos/${draft.id}/update`, method: "POST", data });
    editingTodo.value = null;
    uni.showToast({ title: "已更新", icon: "success" });
    void loadHome();
  } catch (error: unknown) {
    uni.showToast({
      title: error instanceof Error ? error.message : "保存失败，请重试",
      icon: "none",
    });
  } finally {
    todoSaving.value = false;
  }
}

function completeTodo(): void {
  void submitTodo({ completed: true });
}

function onTodoDateChange(e: { detail: { value: string } }): void {
  if (editingTodo.value) editingTodo.value.date = e.detail.value;
}

function onTodoTimeChange(e: { detail: { value: string } }): void {
  if (editingTodo.value) editingTodo.value.time = e.detail.value;
}

onShow(() => {
  applyRoleTabs(auth.user?.role);
  void loadHome();
  void loadGreeting();
  void loadUnread();
  void prefetchSubscribeConfig();
});

onPullDownRefresh(() => {
  void loadHome();
  void loadUnread();
});
</script>

<template>
  <view class="home">
    <view class="hero">
      <view class="hero__top">
        <text class="hero__date">{{ dateLine }}</text>
        <view class="hero__bell press" @tap="openMessages">
          <text>🔔</text>
          <view v-if="unreadTotal" class="hero__badge">
            <text>{{ unreadTotal > 99 ? "99+" : unreadTotal }}</text>
          </view>
        </view>
      </view>
      <text class="hero__hello">{{ greeting || `你好，${auth.user?.name || home?.user?.name || ""}` }}</text>
    </view>

    <view class="glass-card agenda">
      <view class="agenda__head">
        <text class="agenda__title">今天要做的事</text>
        <text v-if="home" class="agenda__count glass-chip">{{ activeAgenda.length }} 项</text>
      </view>

      <view v-if="loading && !home" class="agenda__empty"><text>加载中…</text></view>
      <view v-else-if="failed" class="agenda__empty" @tap="loadHome">
        <text>加载失败，点击重试</text>
      </view>
      <view v-else-if="!activeAgenda.length" class="agenda__empty">
        <text>🎉 暂无待办，好好休息</text>
      </view>

      <view
        v-for="(event, index) in activeAgenda"
        :key="index"
        class="agenda-item press"
        @tap="openAgenda(event)"
      >
        <view class="agenda-item__icon glass-chip">
          <text>{{ KIND_ICONS[event.kind] || "🗓️" }}</text>
        </view>
        <view class="agenda-item__body">
          <text class="agenda-item__title">{{ event.title }}</text>
          <text v-if="event.subtitle" class="agenda-item__subtitle">{{ event.subtitle }}</text>
        </view>
        <view class="agenda-item__when">
          <text class="agenda-item__relative">{{ event.relative_label }}</text>
          <text class="agenda-item__hour">{{ event.hour_label }}</text>
        </view>
      </view>

      <view v-if="pastAgenda.length" class="agenda__history-toggle press" @tap="showHistory = !showHistory">
        <text>{{ showHistory ? "收起历史" : `历史 ${pastAgenda.length} 条` }}</text>
        <text class="agenda__history-arrow">{{ showHistory ? "▴" : "▾" }}</text>
      </view>

      <template v-if="showHistory">
        <view
          v-for="(event, index) in pastAgenda"
          :key="`past-${index}`"
          class="agenda-item agenda-item--past press"
          @tap="openAgenda(event)"
        >
          <view class="agenda-item__icon glass-chip">
            <text>{{ KIND_ICONS[event.kind] || "🗓️" }}</text>
          </view>
          <view class="agenda-item__body">
            <text class="agenda-item__title">{{ event.title }}</text>
            <text v-if="event.subtitle" class="agenda-item__subtitle">{{ event.subtitle }}</text>
          </view>
          <view class="agenda-item__when">
            <text class="agenda-item__relative">{{ event.relative_label }}</text>
            <text class="agenda-item__hour">{{ event.hour_label }}</text>
          </view>
        </view>
      </template>
    </view>

    <!-- 私人待办编辑弹层 -->
    <view v-if="editingTodo" class="todo-mask" @tap="editingTodo = null" />
    <view v-if="editingTodo" class="todo-editor glass-card">
      <text class="todo-editor__title">编辑待办</text>
      <input v-model="editingTodo.title" class="todo-editor__input" placeholder="待办标题" />
      <textarea
        v-model="editingTodo.notes"
        class="todo-editor__notes"
        placeholder="备注（可选）"
        :maxlength="500"
        auto-height
      />
      <view class="todo-editor__row">
        <picker mode="date" :value="editingTodo.date" @change="onTodoDateChange">
          <view class="todo-editor__picker press">
            <text>📅 {{ editingTodo.date || "选择日期" }}</text>
          </view>
        </picker>
        <picker mode="time" :value="editingTodo.time" @change="onTodoTimeChange">
          <view class="todo-editor__picker press">
            <text>🕘 {{ editingTodo.time }}</text>
          </view>
        </picker>
      </view>
      <view class="todo-editor__row todo-editor__row--between">
        <text class="todo-editor__label">到期提醒</text>
        <switch :checked="editingTodo.reminder" color="#5b6ee0" @change="editingTodo.reminder = !editingTodo.reminder" />
      </view>
      <view class="todo-editor__actions">
        <button class="todo-editor__btn todo-editor__btn--done" :disabled="todoSaving" @tap="completeTodo">
          标记完成
        </button>
        <button class="todo-editor__btn glass-btn-primary" :loading="todoSaving" :disabled="todoSaving" @tap="submitTodo()">
          保存
        </button>
      </view>
    </view>

    <view v-if="home?.stats?.length" class="stats">
      <view
        v-for="(stat, index) in home.stats.slice(0, 4)"
        :key="index"
        class="glass-card stat"
        :class="STAT_TONES[index % STAT_TONES.length]"
      >
        <text class="stat__value">{{ stat.value }}</text>
        <text class="stat__label">{{ stat.label }}</text>
      </view>
    </view>
  </view>
</template>

<style scoped>
.home {
  min-height: 100vh;
  padding: 30rpx 30rpx calc(env(safe-area-inset-bottom) + 32rpx);
  display: flex;
  flex-direction: column;
  gap: 30rpx;
}

.hero {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
  padding: 12rpx 8rpx 0;
}

.agenda__history-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8rpx;
  padding: 16rpx 0 4rpx;
  font-size: 24rpx;
  color: #8b96b3;
}

.agenda__history-arrow {
  font-size: 22rpx;
}

.agenda-item--past {
  opacity: 0.55;
}

/* 待办编辑弹层 */
.todo-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  z-index: 90;
}

.todo-editor {
  position: fixed;
  left: 40rpx;
  right: 40rpx;
  top: 20%;
  z-index: 100;
  padding: 34rpx 34rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
  background: rgba(255, 255, 255, 0.96);
}

.todo-editor__title {
  font-size: 30rpx;
  font-weight: 700;
  color: #1b2540;
}

.todo-editor__input {
  border: 2rpx solid rgba(120, 140, 200, 0.28);
  background: rgba(255, 255, 255, 0.8);
  border-radius: 16rpx;
  padding: 18rpx 22rpx;
  font-size: 28rpx;
  color: #1b2540;
}

.todo-editor__notes {
  width: 100%;
  box-sizing: border-box;
  border: 2rpx solid rgba(120, 140, 200, 0.22);
  background: rgba(255, 255, 255, 0.8);
  border-radius: 16rpx;
  padding: 18rpx 22rpx;
  font-size: 25rpx;
  color: #334155;
  min-height: 120rpx;
}

.todo-editor__row {
  display: flex;
  gap: 16rpx;
}

.todo-editor__row--between {
  align-items: center;
  justify-content: space-between;
}

.todo-editor__picker {
  border-radius: 999rpx;
  background: rgba(120, 140, 200, 0.1);
  padding: 14rpx 26rpx;
  font-size: 25rpx;
  color: #2f3d5e;
}

.todo-editor__label {
  font-size: 26rpx;
  color: #334155;
}

.todo-editor__actions {
  display: flex;
  gap: 16rpx;
  padding-top: 6rpx;
}

.todo-editor__btn {
  flex: 1;
  min-height: 80rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999rpx;
  font-size: 27rpx;
  font-weight: 600;
  margin: 0;
}

.todo-editor__btn--done {
  background: rgba(30, 158, 106, 0.1);
  border: 2rpx solid rgba(30, 158, 106, 0.3);
  color: #1e9e6a;
}

.hero__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hero__bell {
  position: relative;
  font-size: 34rpx;
  padding: 4rpx 10rpx;
}

.hero__badge {
  position: absolute;
  top: -8rpx;
  right: -10rpx;
  min-width: 30rpx;
  height: 30rpx;
  border-radius: 999rpx;
  background: #e5484d;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 8rpx;
}

.hero__badge text {
  font-size: 18rpx;
  color: #ffffff;
  font-weight: 700;
}

.hero__date {
  font-size: 24rpx;
  color: #8b96b3;
  letter-spacing: 2rpx;
}

.hero__hello {
  font-size: 42rpx;
  font-weight: 700;
  color: #1b2540;
  line-height: 1.45;
}

.agenda {
  padding: 38rpx 34rpx;
  display: flex;
  flex-direction: column;
  gap: 26rpx;
}

.agenda__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.agenda__title {
  font-size: 34rpx;
  font-weight: 700;
  color: #1b2540;
}

.agenda__count {
  font-size: 23rpx;
  color: #66718f;
  padding: 8rpx 22rpx;
}

.agenda__empty {
  padding: 52rpx 0;
  text-align: center;
  color: #8b96b3;
  font-size: 28rpx;
}

.agenda-item {
  display: flex;
  align-items: center;
  gap: 22rpx;
}

.agenda-item__icon {
  width: 76rpx;
  height: 76rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 36rpx;
  border-radius: 24rpx;
  flex-shrink: 0;
}

.agenda-item__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.agenda-item__title {
  font-size: 30rpx;
  font-weight: 550;
  color: #1b2540;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agenda-item__subtitle {
  font-size: 23rpx;
  color: #8b96b3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agenda-item__when {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6rpx;
  flex-shrink: 0;
}

.agenda-item__relative {
  font-size: 26rpx;
  font-weight: 700;
  color: #4a7dff;
}

.agenda-item__hour {
  font-size: 22rpx;
  color: #8b96b3;
}

.stats {
  display: flex;
  gap: 20rpx;
}

.stat {
  flex: 1;
  padding: 30rpx 0 26rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
  border-radius: 30rpx;
}

.stat__value {
  font-size: 40rpx;
  font-weight: 800;
  color: #1b2540;
}

.stat__label {
  font-size: 22rpx;
  color: #8b96b3;
}

.tone-blue .stat__value {
  color: #2f5ee0;
}

.tone-orange .stat__value {
  color: #d05a1f;
}

.tone-green .stat__value {
  color: #1e9e6a;
}

.tone-purple .stat__value {
  color: #7c4fd0;
}
</style>
