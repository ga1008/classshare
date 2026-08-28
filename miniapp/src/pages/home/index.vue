<script setup lang="ts">
/**
 * 首页 = "今天"：问候 + 日期 + 大议程玻璃卡 + 彩色统计块。
 * 数据源 /api/mp/home（学生的待完成/已提交与任务列表同源对齐）。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";
import { useAuthStore } from "../../stores/auth";

interface AgendaEvent {
  kind: string;
  title: string;
  subtitle: string;
  hour_label: string;
  relative_label: string;
  href?: string;
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

const todayAgenda = computed(() => (home.value?.agenda ?? []).slice(0, 8));

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
  // 作业/考试类议程直达作答页；href 形如 /assignment/12 或 /exam/take/12
  const match = /\/(?:assignment|exam\/take)\/(\d+)/.exec(event.href || "");
  if (match) {
    uni.navigateTo({ url: `/pages/task-detail/index?id=${match[1]}` });
    return;
  }
  uni.switchTab({ url: "/pages/tasks/index" });
}

onShow(() => {
  void loadHome();
  void loadGreeting();
});

onPullDownRefresh(() => {
  void loadHome();
});
</script>

<template>
  <view class="home">
    <view class="hero">
      <text class="hero__date">{{ dateLine }}</text>
      <text class="hero__hello">{{ greeting || `你好，${auth.user?.name || home?.user?.name || ""}` }}</text>
    </view>

    <view class="glass-card agenda">
      <view class="agenda__head">
        <text class="agenda__title">今天要做的事</text>
        <text v-if="home" class="agenda__count glass-chip">{{ todayAgenda.length }} 项</text>
      </view>

      <view v-if="loading && !home" class="agenda__empty"><text>加载中…</text></view>
      <view v-else-if="failed" class="agenda__empty" @tap="loadHome">
        <text>加载失败，点击重试</text>
      </view>
      <view v-else-if="!todayAgenda.length" class="agenda__empty">
        <text>🎉 暂无待办，好好休息</text>
      </view>

      <view
        v-for="(event, index) in todayAgenda"
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
