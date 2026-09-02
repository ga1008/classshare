<script setup lang="ts">
/**
 * 我的：玻璃头像卡 + 身份信息行 + 绑定说明 + 退出。
 */
import { onShow } from "@dcloudio/uni-app";
import { computed } from "vue";

import { request } from "../../utils/api";
import { useAuthStore, type MpUser } from "../../stores/auth";
import { applyRoleTabs, resetRoleTabs } from "../../utils/tabs";

const auth = useAuthStore();

/** 功能列表：M3/M5 里程碑逐项点亮；未上线项点击给提示。 */
interface FeatureEntry {
  icon: string;
  title: string;
  desc: string;
  url?: string;
}

const featureEntries = computed<FeatureEntry[]>(() => {
  if (auth.user?.role === "teacher") {
    return [
      { icon: "📅", title: "本周课表", desc: "今天在哪上课，一眼看到" },
      { icon: "📣", title: "催交中心", desc: "未交汇总，一键提醒" },
      { icon: "🔔", title: "消息中心", desc: "平台通知都在这里", url: "/pages/messages/index" },
    ];
  }
  return [
    { icon: "🏆", title: "成绩单", desc: "学期成绩概览", url: "/pages/report-card/index" },
    { icon: "📖", title: "错题本", desc: "错过的题再看一遍", url: "/pages/wrong-book/index" },
    { icon: "⚔️", title: "修为与积分", desc: "徽章、积分与商店", url: "/pages/growth/index" },
    { icon: "🔔", title: "消息中心", desc: "平台通知都在这里", url: "/pages/messages/index" },
    { icon: "🤖", title: "AI 助手", desc: "随时随地问学业问题" },
  ];
});

function openFeature(entry: FeatureEntry): void {
  if (entry.url) {
    uni.navigateTo({ url: entry.url });
    return;
  }
  uni.showToast({ title: "即将上线，敬请期待", icon: "none" });
}

const infoRows = computed(() => {
  const user = auth.user;
  if (!user) return [];
  if (user.role === "teacher") {
    return [
      { label: "身份", value: "教师" },
      { label: "邮箱", value: user.email || "—" },
    ];
  }
  return [
    { label: "身份", value: "学生" },
    { label: "班级", value: user.class_name || "—" },
    { label: "学号", value: user.student_id_number || "—" },
  ];
});

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
      content: "退出将解除微信绑定，下次进入需重新绑定账号。确定退出吗？",
      confirmColor: "#e5484d",
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
  if (!confirmed) return;
  await auth.logout();
  resetRoleTabs();
  uni.reLaunch({ url: "/pages/welcome/index" });
}

onShow(() => {
  void ensureSession().then(() => {
    applyRoleTabs(auth.user?.role);
    if (auth.user?.role === "teacher") {
      uni.setNavigationBarTitle({ title: "工作台" });
    }
  });
});
</script>

<template>
  <view class="me">
    <view v-if="auth.user" class="glass-card profile">
      <view class="profile__avatar">
        <text>{{ auth.user.name?.slice(0, 1) }}</text>
      </view>
      <view class="profile__body">
        <text class="profile__name">{{ auth.user.name }}</text>
        <text class="profile__meta">
          {{ auth.user.role === "teacher" ? "教师" : `${auth.user.class_name || ""}` }}
        </text>
      </view>
      <view class="profile__wx glass-chip">
        <text>✓ 微信已绑定</text>
      </view>
    </view>

    <view v-if="infoRows.length" class="glass-card info">
      <view v-for="row in infoRows" :key="row.label" class="info-row">
        <text class="info-row__label">{{ row.label }}</text>
        <text class="info-row__value">{{ row.value }}</text>
      </view>
    </view>

    <view class="glass-card features">
      <view
        v-for="entry in featureEntries"
        :key="entry.title"
        class="feature-row press"
        @tap="openFeature(entry)"
      >
        <text class="feature-row__icon">{{ entry.icon }}</text>
        <view class="feature-row__body">
          <text class="feature-row__title">{{ entry.title }}</text>
          <text class="feature-row__desc">{{ entry.desc }}</text>
        </view>
        <text v-if="!entry.url" class="feature-row__badge">即将上线</text>
        <text v-else class="feature-row__arrow">›</text>
      </view>
    </view>

    <view class="glass-card note">
      <text class="note__title">自动登录</text>
      <text class="note__text">
        每次进入小程序将自动登录，无需输入账号。如需换绑其他账号，退出登录后重新绑定即可。
      </text>
    </view>

    <button class="logout press" @tap="handleLogout">退出登录</button>

    <text class="footer">LanShare 智慧课堂 · 让课堂更轻</text>
  </view>
</template>

<style scoped>
.me {
  min-height: 100vh;
  padding: 36rpx 30rpx calc(env(safe-area-inset-bottom) + 32rpx);
  display: flex;
  flex-direction: column;
  gap: 26rpx;
}

.profile {
  padding: 42rpx 36rpx;
  display: flex;
  align-items: center;
  gap: 26rpx;
}

.profile__avatar {
  width: 112rpx;
  height: 112rpx;
  border-radius: 40rpx;
  background: linear-gradient(135deg, #5b8cff, #7c4fd0);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  font-size: 46rpx;
  font-weight: 700;
  box-shadow: 0 10rpx 26rpx rgba(92, 110, 220, 0.35);
  flex-shrink: 0;
}

.profile__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.profile__name {
  font-size: 38rpx;
  font-weight: 700;
  color: #1b2540;
}

.profile__meta {
  font-size: 25rpx;
  color: #8b96b3;
}

.profile__wx {
  padding: 10rpx 20rpx;
  font-size: 22rpx;
  color: #1e9e6a;
  flex-shrink: 0;
}

.info {
  padding: 12rpx 36rpx;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 28rpx 0;
}

.info-row + .info-row {
  border-top: 1rpx solid rgba(130, 148, 200, 0.14);
}

.info-row__label {
  font-size: 27rpx;
  color: #66718f;
}

.info-row__value {
  font-size: 27rpx;
  font-weight: 600;
  color: #1b2540;
}

.features {
  padding: 8rpx 32rpx;
}

.feature-row {
  display: flex;
  align-items: center;
  gap: 22rpx;
  padding: 26rpx 4rpx;
}

.feature-row + .feature-row {
  border-top: 1rpx solid rgba(130, 148, 200, 0.14);
}

.feature-row__icon {
  font-size: 40rpx;
  flex-shrink: 0;
}

.feature-row__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.feature-row__title {
  font-size: 28rpx;
  font-weight: 600;
  color: #1b2540;
}

.feature-row__desc {
  font-size: 22rpx;
  color: #9aa6bf;
}

.feature-row__badge {
  flex-shrink: 0;
  font-size: 20rpx;
  color: #b08a2e;
  background: rgba(240, 195, 90, 0.16);
  border: 1rpx solid rgba(240, 195, 90, 0.35);
  border-radius: 999rpx;
  padding: 6rpx 16rpx;
}

.feature-row__arrow {
  flex-shrink: 0;
  font-size: 36rpx;
  color: #aab3c9;
}

.note {
  padding: 32rpx 36rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.note__title {
  font-size: 27rpx;
  font-weight: 700;
  color: #1b2540;
}

.note__text {
  font-size: 24rpx;
  color: #8b96b3;
  line-height: 1.7;
}

.logout {
  margin-top: auto;
  min-height: 94rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  border: 1rpx solid rgba(229, 72, 77, 0.25);
  color: #e5484d;
  font-size: 30rpx;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.footer {
  text-align: center;
  font-size: 21rpx;
  color: #aab3c9;
  padding-top: 6rpx;
}
</style>
