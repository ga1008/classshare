<script setup lang="ts">
/**
 * 我的：身份信息 + 退出登录。次要功能的统一收纳位（后续扩展）。
 */
import { onShow } from "@dcloudio/uni-app";

import { request } from "../../utils/api";
import { useAuthStore, type MpUser } from "../../stores/auth";

const auth = useAuthStore();

async function ensureSession(): Promise<void> {
  if (auth.user) return;
  try {
    const data = await request<{ user: MpUser }>({ path: "/api/mp/auth/me" });
    auth.user = data.user;
  } catch {
    uni.reLaunch({ url: "/pages/welcome/index" });
  }
}

async function handleLogout(): Promise<void> {
  const confirmed = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: "退出登录",
      content: "退出后需要重新绑定才能进入，确定退出吗？",
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
  if (!confirmed) return;
  await auth.logout();
  uni.reLaunch({ url: "/pages/welcome/index" });
}

onShow(() => {
  void ensureSession();
});
</script>

<template>
  <view class="me">
    <view v-if="auth.user" class="profile-card">
      <view class="profile-card__avatar">
        <text>{{ auth.user.name?.slice(0, 1) }}</text>
      </view>
      <view class="profile-card__body">
        <text class="profile-card__name">{{ auth.user.name }}</text>
        <text class="profile-card__meta">
          {{ auth.user.role === "teacher" ? `教师 · ${auth.user.email || ""}` : `${auth.user.class_name || ""} · ${auth.user.student_id_number || ""}` }}
        </text>
      </view>
    </view>

    <view class="tip-card">
      <text class="tip-card__title">微信已绑定</text>
      <text class="tip-card__text">每次进入小程序将自动登录，无需输入账号。如需换绑，请退出登录后重新绑定。</text>
    </view>

    <button class="logout-btn" @tap="handleLogout">退出登录</button>
  </view>
</template>

<style scoped>
.me {
  min-height: 100vh;
  padding: 40rpx 32rpx calc(env(safe-area-inset-bottom) + 32rpx);
  background: #f4f6fb;
  display: flex;
  flex-direction: column;
  gap: 28rpx;
}

.profile-card {
  background: #ffffff;
  border-radius: 36rpx;
  padding: 44rpx 36rpx;
  display: flex;
  align-items: center;
  gap: 28rpx;
  box-shadow: 0 8rpx 32rpx rgba(15, 23, 42, 0.06);
}

.profile-card__avatar {
  width: 108rpx;
  height: 108rpx;
  border-radius: 50%;
  background: #4a7dff;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  font-size: 44rpx;
  font-weight: 600;
}

.profile-card__body {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.profile-card__name {
  font-size: 38rpx;
  font-weight: 600;
  color: #16213a;
}

.profile-card__meta {
  font-size: 26rpx;
  color: #94a3b8;
}

.tip-card {
  background: #ffffff;
  border-radius: 32rpx;
  padding: 36rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.tip-card__title {
  font-size: 28rpx;
  font-weight: 600;
  color: #16213a;
}

.tip-card__text {
  font-size: 24rpx;
  color: #94a3b8;
  line-height: 1.7;
}

.logout-btn {
  margin-top: auto;
  min-height: 92rpx;
  border-radius: 999rpx;
  background: #ffffff;
  color: #ef4444;
  font-size: 30rpx;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
