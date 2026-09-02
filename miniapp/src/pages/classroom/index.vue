<script setup lang="ts">
/**
 * 课堂 tab（M2）：进行中课堂置顶 + 我的课堂列表。
 *
 * 数据源 /api/mp/classroom/live（轻聚合：各课堂进行中投票/互动/求助计数）。
 * tab 在前台时每 10s 轮询一次（不上 WebSocket：服务器 2c/4GB 与小程序
 * socket 复杂度都不划算，200 并发量级轮询可承受）。
 * 签到来自智慧课堂外部同步，平台无原生签到，小程序不另造。
 */
import { onHide, onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";
import { useAuthStore } from "../../stores/auth";
import { applyRoleTabs } from "../../utils/tabs";

interface LiveOffering {
  id: number;
  course_name: string;
  class_name: string;
  teacher_name: string;
  student_count: number;
  active_poll_count: number;
  active_activity_count: number;
  active_signal_count: number;
  my_signal: string;
  is_live: boolean;
}

const POLL_INTERVAL_MS = 10_000;

const auth = useAuthStore();
const offerings = ref<LiveOffering[]>([]);
const loading = ref(true);
const failed = ref(false);
let timer: ReturnType<typeof setInterval> | null = null;

const isTeacher = computed(() => auth.user?.role === "teacher");
const liveOfferings = computed(() => offerings.value.filter((item) => item.is_live));
const idleOfferings = computed(() => offerings.value.filter((item) => !item.is_live));

async function loadLive(silent = false): Promise<void> {
  if (!silent) loading.value = true;
  try {
    const data = await request<{ offerings: LiveOffering[] }>({ path: "/api/mp/classroom/live" });
    offerings.value = data.offerings ?? [];
    failed.value = false;
  } catch (error: unknown) {
    if (!silent) failed.value = true;
    if ((error as { statusCode?: number }).statusCode === 401) {
      uni.reLaunch({ url: "/pages/welcome/index" });
    }
  } finally {
    loading.value = false;
    uni.stopPullDownRefresh();
  }
}

function openLive(item: LiveOffering): void {
  const title = encodeURIComponent(`${item.course_name} · ${item.class_name}`);
  uni.navigateTo({ url: `/pages/live/index?oid=${item.id}&title=${title}` });
}

function liveSummary(item: LiveOffering): string {
  const parts: string[] = [];
  if (item.active_poll_count) parts.push(`${item.active_poll_count} 个投票`);
  if (item.active_activity_count) parts.push(`${item.active_activity_count} 个互动`);
  if (isTeacher.value && item.active_signal_count) parts.push(`${item.active_signal_count} 人举手/求助`);
  return parts.join(" · ");
}

onShow(() => {
  applyRoleTabs(auth.user?.role);
  void loadLive();
  if (timer) clearInterval(timer);
  timer = setInterval(() => void loadLive(true), POLL_INTERVAL_MS);
});

onHide(() => {
  if (timer) clearInterval(timer);
  timer = null;
});

onPullDownRefresh(() => {
  void loadLive(true);
});
</script>

<template>
  <view class="classroom">
    <view class="glass-card live">
      <view class="live__head">
        <text class="live__title">进行中</text>
        <text class="live__hint glass-chip">{{ liveOfferings.length ? `${liveOfferings.length} 个课堂` : "实时" }}</text>
      </view>

      <view v-if="loading && !offerings.length" class="live__empty"><text>加载中…</text></view>
      <view v-else-if="failed && !offerings.length" class="live__empty" @tap="loadLive()">
        <text class="live__empty-text">加载失败，点击重试</text>
      </view>
      <view v-else-if="!liveOfferings.length" class="live__empty">
        <text class="live__empty-icon">🏫</text>
        <text class="live__empty-text">当前没有进行中的课堂事项</text>
        <text class="live__empty-sub">{{ isTeacher ? "进入课堂即可发起投票或随堂测" : "老师发起投票、随堂互动后会出现在这里" }}</text>
      </view>

      <view v-for="item in liveOfferings" :key="item.id" class="live-item press" @tap="openLive(item)">
        <view class="live-item__pulse" />
        <view class="live-item__body">
          <text class="live-item__title">{{ item.course_name }}</text>
          <text class="live-item__meta">{{ item.class_name }} · {{ liveSummary(item) }}</text>
        </view>
        <text class="live-item__arrow">›</text>
      </view>
    </view>

    <view class="section-title"><text>我的课堂</text></view>

    <view v-if="!loading && !offerings.length" class="glass-card feature">
      <text class="feature__desc">还没有课堂，请联系老师或管理员开课。</text>
    </view>

    <view
      v-for="item in idleOfferings"
      :key="item.id"
      class="glass-card feature press"
      @tap="openLive(item)"
    >
      <text class="feature__icon">📘</text>
      <view class="feature__body">
        <text class="feature__title">{{ item.course_name }}</text>
        <text class="feature__desc">
          {{ item.class_name }}
          <template v-if="isTeacher"> · {{ item.student_count }} 人</template>
          <template v-else-if="item.teacher_name"> · {{ item.teacher_name }}</template>
        </text>
      </view>
      <text class="feature__arrow">›</text>
    </view>
  </view>
</template>

<style scoped>
.classroom {
  min-height: 100vh;
  padding: 36rpx 30rpx calc(env(safe-area-inset-bottom) + 32rpx);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.live {
  padding: 32rpx 36rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.live__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.live__title {
  font-size: 32rpx;
  font-weight: 700;
  color: #1b2540;
}

.live__hint {
  padding: 8rpx 18rpx;
  font-size: 21rpx;
  color: #66718f;
}

.live__empty {
  padding: 44rpx 0 24rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
}

.live__empty-icon {
  font-size: 64rpx;
}

.live__empty-text {
  font-size: 28rpx;
  font-weight: 600;
  color: #4a5878;
}

.live__empty-sub {
  font-size: 23rpx;
  color: #9aa6bf;
  text-align: center;
}

.live-item {
  display: flex;
  align-items: center;
  gap: 20rpx;
  padding: 22rpx 24rpx;
  border-radius: 20rpx;
  background: rgba(30, 158, 106, 0.08);
  border: 1rpx solid rgba(30, 158, 106, 0.22);
}

.live-item__pulse {
  width: 16rpx;
  height: 16rpx;
  border-radius: 999rpx;
  background: #1e9e6a;
  box-shadow: 0 0 0 8rpx rgba(30, 158, 106, 0.18);
  flex-shrink: 0;
}

.live-item__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.live-item__title {
  font-size: 29rpx;
  font-weight: 700;
  color: #1b2540;
}

.live-item__meta {
  font-size: 23rpx;
  color: #1e9e6a;
}

.live-item__arrow,
.feature__arrow {
  font-size: 36rpx;
  color: #aab3c9;
  flex-shrink: 0;
}

.section-title {
  padding: 8rpx 8rpx 0;
  font-size: 26rpx;
  font-weight: 700;
  color: #66718f;
}

.feature {
  padding: 30rpx 32rpx;
  display: flex;
  align-items: center;
  gap: 24rpx;
}

.feature__icon {
  font-size: 44rpx;
  flex-shrink: 0;
}

.feature__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.feature__title {
  font-size: 29rpx;
  font-weight: 700;
  color: #1b2540;
}

.feature__desc {
  font-size: 23rpx;
  color: #8b96b3;
}
</style>
