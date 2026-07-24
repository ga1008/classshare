<script setup lang="ts">
/**
 * 首页 = "今天"：个性化欢迎语 + 一张大议程卡 + 统计大数字。
 * 数据源 /api/mp/home（复用 Web dashboard 单一真源）。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";
import { useAuthStore } from "../../stores/auth";

interface AgendaEvent {
  kind: string;
  title: string;
  subtitle: string;
  date_label: string;
  hour_label: string;
  relative_label: string;
  status: string;
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

const auth = useAuthStore();
const home = ref<HomeData | null>(null);
const greeting = ref("");
const loading = ref(false);
const failed = ref(false);

const todayAgenda = computed(() => (home.value?.agenda ?? []).slice(0, 8));

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
      <text class="hero__hello">{{ greeting || `你好，${auth.user?.name || home?.user?.name || ""}` }}</text>
    </view>

    <view class="agenda-card">
      <view class="agenda-card__head">
        <text class="agenda-card__title">今天要做的事</text>
        <text v-if="home" class="agenda-card__count">{{ todayAgenda.length }} 项</text>
      </view>

      <view v-if="loading && !home" class="agenda-card__empty">
        <text>加载中…</text>
      </view>
      <view v-else-if="failed" class="agenda-card__empty" @tap="loadHome">
        <text>加载失败，点击重试</text>
      </view>
      <view v-else-if="!todayAgenda.length" class="agenda-card__empty">
        <text>🎉 暂无待办，好好休息</text>
      </view>

      <view v-for="(event, index) in todayAgenda" :key="index" class="agenda-item">
        <text class="agenda-item__icon">{{ KIND_ICONS[event.kind] || "🗓️" }}</text>
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
      <view v-for="(stat, index) in home.stats.slice(0, 4)" :key="index" class="stat-card">
        <text class="stat-card__value">{{ stat.value }}</text>
        <text class="stat-card__label">{{ stat.label }}</text>
      </view>
    </view>
  </view>
</template>

<style scoped>
.home {
  min-height: 100vh;
  padding: 32rpx 32rpx calc(env(safe-area-inset-bottom) + 32rpx);
  background: #f4f6fb;
  display: flex;
  flex-direction: column;
  gap: 32rpx;
}

.hero {
  padding: 16rpx 8rpx;
}

.hero__hello {
  font-size: 40rpx;
  font-weight: 600;
  color: #16213a;
  line-height: 1.5;
}

.agenda-card {
  background: #ffffff;
  border-radius: 36rpx;
  padding: 40rpx 36rpx;
  box-shadow: 0 8rpx 32rpx rgba(15, 23, 42, 0.06);
  display: flex;
  flex-direction: column;
  gap: 28rpx;
}

.agenda-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.agenda-card__title {
  font-size: 34rpx;
  font-weight: 600;
  color: #16213a;
}

.agenda-card__count {
  font-size: 24rpx;
  color: #94a3b8;
}

.agenda-card__empty {
  padding: 48rpx 0;
  text-align: center;
  color: #94a3b8;
  font-size: 28rpx;
}

.agenda-item {
  display: flex;
  align-items: center;
  gap: 24rpx;
}

.agenda-item__icon {
  font-size: 44rpx;
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
  color: #16213a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agenda-item__subtitle {
  font-size: 24rpx;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agenda-item__when {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6rpx;
}

.agenda-item__relative {
  font-size: 26rpx;
  color: #4a7dff;
  font-weight: 600;
}

.agenda-item__hour {
  font-size: 22rpx;
  color: #94a3b8;
}

.stats {
  display: flex;
  gap: 20rpx;
}

.stat-card {
  flex: 1;
  background: #ffffff;
  border-radius: 28rpx;
  padding: 28rpx 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
  box-shadow: 0 8rpx 32rpx rgba(15, 23, 42, 0.04);
}

.stat-card__value {
  font-size: 40rpx;
  font-weight: 700;
  color: #16213a;
}

.stat-card__label {
  font-size: 22rpx;
  color: #94a3b8;
}
</style>
