<script setup lang="ts">
/**
 * 课堂 tab（M0 结构壳）：进行中事项区 + 功能预告区。
 * M2 里程碑将接入：签到、投票参与、课堂互动答题（学生）；发起签到/投票（教师）。
 * M0 不调后端，仅把信息架构立起来。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed } from "vue";

import { useAuthStore } from "../../stores/auth";
import { applyRoleTabs } from "../../utils/tabs";

const auth = useAuthStore();
const isTeacher = computed(() => auth.user?.role === "teacher");

const upcoming = computed(() =>
  isTeacher.value
    ? [
        { icon: "✅", title: "发起签到", desc: "上课时一键发起，实时看到课统计" },
        { icon: "🗳️", title: "投票开闸", desc: "把 Web 端建好的投票一键开始/结束" },
        { icon: "⚡", title: "课堂互动", desc: "随堂提问，学生手机即答" },
      ]
    : [
        { icon: "✅", title: "课堂签到", desc: "老师发起后，这里一键签到" },
        { icon: "🗳️", title: "课堂投票", desc: "进行中的投票直接投" },
        { icon: "⚡", title: "随堂作答", desc: "老师随堂提问，掏出手机就能答" },
      ],
);

function comingSoon(): void {
  uni.showToast({ title: "即将上线，敬请期待", icon: "none" });
}

onShow(() => {
  applyRoleTabs(auth.user?.role);
});

onPullDownRefresh(() => {
  uni.stopPullDownRefresh();
});
</script>

<template>
  <view class="classroom">
    <view class="glass-card live">
      <view class="live__head">
        <text class="live__title">进行中</text>
        <text class="live__hint glass-chip">实时</text>
      </view>
      <view class="live__empty">
        <text class="live__empty-icon">🏫</text>
        <text class="live__empty-text">当前没有进行中的课堂事项</text>
        <text class="live__empty-sub">签到、投票、随堂互动开始后会出现在这里</text>
      </view>
    </view>

    <view class="section-title">
      <text>{{ isTeacher ? "课堂工具" : "课堂功能" }}</text>
    </view>

    <view
      v-for="item in upcoming"
      :key="item.title"
      class="glass-card feature press"
      @tap="comingSoon"
    >
      <text class="feature__icon">{{ item.icon }}</text>
      <view class="feature__body">
        <text class="feature__title">{{ item.title }}</text>
        <text class="feature__desc">{{ item.desc }}</text>
      </view>
      <text class="feature__badge">即将上线</text>
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
  padding: 56rpx 0 32rpx;
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
  font-size: 48rpx;
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

.feature__badge {
  flex-shrink: 0;
  font-size: 20rpx;
  color: #b08a2e;
  background: rgba(240, 195, 90, 0.16);
  border: 1rpx solid rgba(240, 195, 90, 0.35);
  border-radius: 999rpx;
  padding: 6rpx 16rpx;
}
</style>
