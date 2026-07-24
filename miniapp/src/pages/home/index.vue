<script setup lang="ts">
/**
 * 首页（P1 占位版）：展示登录身份，验证全链路。
 * P2 将替换为"今天"大议程卡 + tabBar 信息架构。
 */
import { onShow } from "@dcloudio/uni-app";
import { ref } from "vue";

import { request } from "../../utils/api";
import { useAuthStore, type MpUser } from "../../stores/auth";

const auth = useAuthStore();
const checking = ref(false);

async function ensureSession(): Promise<void> {
  if (auth.user) return;
  checking.value = true;
  try {
    const data = await request<{ user: MpUser }>({ path: "/api/mp/auth/me" });
    auth.user = data.user;
  } catch {
    uni.reLaunch({ url: "/pages/welcome/index" });
  } finally {
    checking.value = false;
  }
}

async function handleLogout(): Promise<void> {
  await auth.logout();
  uni.reLaunch({ url: "/pages/welcome/index" });
}

onShow(() => {
  void ensureSession();
});
</script>

<template>
  <view class="home">
    <view v-if="auth.user" class="card">
      <text class="card__greeting">你好，{{ auth.user.name }}</text>
      <text class="card__meta">
        {{ auth.user.role === "teacher" ? "教师" : auth.user.class_name }}
      </text>
      <text class="card__note">微信已绑定，下次进入将自动登录。</text>
    </view>
    <view v-else-if="checking" class="card">
      <text class="card__meta">正在确认登录状态…</text>
    </view>

    <button class="logout-btn" @tap="handleLogout">退出登录</button>
  </view>
</template>

<style scoped>
.home {
  min-height: 100vh;
  padding: 48rpx 32rpx;
  display: flex;
  flex-direction: column;
  gap: 32rpx;
  background: #f4f6fb;
}

.card {
  background: #ffffff;
  border-radius: 32rpx;
  padding: 48rpx 40rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  box-shadow: 0 8rpx 32rpx rgba(15, 23, 42, 0.06);
}

.card__greeting {
  font-size: 44rpx;
  font-weight: 600;
  color: #16213a;
}

.card__meta {
  font-size: 28rpx;
  color: #64748b;
}

.card__note {
  font-size: 24rpx;
  color: #94a3b8;
}

.logout-btn {
  margin-top: auto;
  margin-bottom: calc(env(safe-area-inset-bottom) + 24rpx);
  min-height: 88rpx;
  border-radius: 999rpx;
  background: #ffffff;
  color: #ef4444;
  font-size: 30rpx;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
