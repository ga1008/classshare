<script setup lang="ts">
/**
 * 登录欢迎屏："人生一言"游戏加载屏（复刻 Web 端第五代定稿）。
 *
 * - 背景图 aspectFill：上下顶满手机屏，横图按原比例居中裁竖条；
 * - 色调自适应液态玻璃卡：采样图片中央横带亮度（阈值 148）→
 *   亮图白玻璃深字 / 暗图黑玻璃白字；采样失败回落暗色；
 * - 整句渐显 + 尾段渐隐、底部计时条、点击跳过、👍/👎 反馈；
 * - 展示时长 = 2800ms + 字数×80ms，clamp 3–8s。
 */
import { computed, ref } from "vue";
import { onLoad } from "@dcloudio/uni-app";

import { TIP_SEEN_LIMIT, TIP_SEEN_STORAGE_KEY } from "../../config";
import { request } from "../../utils/api";
import { useAuthStore, type LifeTip } from "../../stores/auth";

type Phase = "loading" | "tip" | "error";
type Tone = "dark" | "light";

const auth = useAuthStore();
const phase = ref<Phase>("loading");
const errorMessage = ref("");
const tip = ref<LifeTip | null>(null);
const tone = ref<Tone>("dark");
const imageReady = ref(false);
const textVisible = ref(false);
const leaving = ref(false);
const feedbackGiven = ref<1 | -1 | 0>(0);

const durationMs = computed(() => {
  const length = tip.value?.text?.length ?? 0;
  return Math.min(8000, Math.max(3000, 2800 + length * 80));
});

const identityLabel = computed(() => {
  const user = auth.user;
  if (!user) return "";
  if (user.role === "teacher") return `${user.name} · 教师`;
  return `${user.name} · ${user.class_name || ""}`;
});

let exitTimer: ReturnType<typeof setTimeout> | null = null;

function readSeenIds(): number[] {
  try {
    const raw = uni.getStorageSync(TIP_SEEN_STORAGE_KEY);
    return Array.isArray(raw) ? (raw as number[]) : [];
  } catch {
    return [];
  }
}

function markSeen(tipId: number): void {
  try {
    const seen = [tipId, ...readSeenIds().filter((id) => id !== tipId)];
    uni.setStorageSync(TIP_SEEN_STORAGE_KEY, seen.slice(0, TIP_SEEN_LIMIT));
  } catch {
    /* 去重失败不影响展示 */
  }
}

function pickTip(candidates: LifeTip[]): LifeTip | null {
  if (!candidates.length) return null;
  const seen = new Set(readSeenIds());
  return candidates.find((item) => !seen.has(item.id)) ?? candidates[0];
}

/** 采样背景图中央横带平均亮度决定玻璃卡色调（与 Web 端 sampleImageTone 同算法）。 */
async function sampleTone(imageUrl: string): Promise<Tone> {
  try {
    const info = await uni.getImageInfo({ src: imageUrl });
    // @ts-expect-error wx offscreen canvas 无 uni 类型
    const canvas = wx.createOffscreenCanvas({ type: "2d", width: 64, height: 64 });
    const ctx = canvas.getContext("2d");
    const image = canvas.createImage();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("image load failed"));
      image.src = info.path;
    });
    ctx.drawImage(image, 0, 0, 64, 64);
    // 中央横带：y 24–40，全宽。
    const band = ctx.getImageData(0, 24, 64, 16).data;
    let total = 0;
    const pixels = band.length / 4;
    for (let i = 0; i < band.length; i += 4) {
      total += 0.299 * band[i] + 0.587 * band[i + 1] + 0.114 * band[i + 2];
    }
    return total / pixels >= 148 ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function scheduleExit(): void {
  if (exitTimer) clearTimeout(exitTimer);
  exitTimer = setTimeout(() => beginExit(), durationMs.value);
}

function beginExit(): void {
  if (leaving.value) return;
  leaving.value = true;
  if (exitTimer) clearTimeout(exitTimer);
  setTimeout(() => {
    uni.reLaunch({ url: "/pages/home/index" });
  }, 420);
}

function onSkipTap(): void {
  if (phase.value === "tip") beginExit();
}

async function sendFeedback(verdict: 1 | -1): Promise<void> {
  if (!tip.value || feedbackGiven.value === verdict) return;
  feedbackGiven.value = verdict;
  try {
    await request({
      path: "/api/mp/life-tips/feedback",
      method: "POST",
      data: { tip_id: tip.value.id, verdict },
    });
  } catch {
    /* 反馈是锦上添花，失败静默 */
  }
}

async function showTipScreen(): Promise<void> {
  const chosen = pickTip(auth.loginTips);
  if (!chosen) {
    uni.reLaunch({ url: "/pages/home/index" });
    return;
  }
  tip.value = chosen;
  markSeen(chosen.id);
  if (chosen.image_url) {
    tone.value = await sampleTone(chosen.image_url);
  }
  phase.value = "tip";
  setTimeout(() => {
    textVisible.value = true;
  }, 200);
  scheduleExit();
}

async function boot(): Promise<void> {
  phase.value = "loading";
  errorMessage.value = "";
  try {
    const result = await auth.silentLogin();
    if (result === "need_bind") {
      uni.redirectTo({ url: "/pages/bind/index" });
      return;
    }
    await showTipScreen();
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : "登录失败，请重试。";
    phase.value = "error";
  }
}

onLoad(() => {
  void boot();
});
</script>

<template>
  <view class="stage" :class="`stage--${tone}`" @tap="onSkipTap">
    <!-- 背景：aspectFill = 竖屏顶天立地、横图居中裁切 -->
    <image
      v-if="tip?.image_url"
      class="stage__bg"
      :class="{ 'stage__bg--ready': imageReady, 'stage__bg--leaving': leaving }"
      :src="tip.image_url"
      mode="aspectFill"
      @load="imageReady = true"
    />

    <!-- 加载态 -->
    <view v-if="phase === 'loading'" class="center-box">
      <view class="loading-dot" />
      <text class="loading-text">正在进入课堂…</text>
    </view>

    <!-- 错误态 -->
    <view v-else-if="phase === 'error'" class="center-box" @tap.stop>
      <text class="error-text">{{ errorMessage }}</text>
      <button class="retry-btn" @tap.stop="boot">重试</button>
    </view>

    <!-- 提示屏 -->
    <template v-else-if="phase === 'tip' && tip">
      <view v-if="identityLabel" class="badge" :class="{ 'fade-out': leaving }">
        <text>{{ identityLabel }}</text>
      </view>

      <view
        class="glass-card"
        :class="[`glass-card--${tone}`, { 'glass-card--visible': textVisible, 'fade-out': leaving }]"
      >
        <text class="glass-card__category">{{ tip.category }}</text>
        <text class="glass-card__text">{{ tip.text }}</text>
        <text v-if="tip.source_ref" class="glass-card__source">{{ tip.source_ref }}</text>
        <view class="glass-card__actions" @tap.stop>
          <view
            class="feedback-btn"
            :class="{ 'feedback-btn--active': feedbackGiven === 1 }"
            @tap.stop="sendFeedback(1)"
          >
            <text>👍 有用</text>
          </view>
          <view
            class="feedback-btn"
            :class="{ 'feedback-btn--active': feedbackGiven === -1 }"
            @tap.stop="sendFeedback(-1)"
          >
            <text>👎 无感</text>
          </view>
        </view>
      </view>

      <view class="timer" :class="{ 'fade-out': leaving }">
        <view class="timer__fill" :style="{ animationDuration: `${durationMs}ms` }" />
      </view>
      <text class="skip-hint" :class="{ 'fade-out': leaving }">点击任意处跳过</text>
    </template>
  </view>
</template>

<style scoped>
.stage {
  position: relative;
  width: 100vw;
  height: 100vh;
  background: #0b1220;
  overflow: hidden;
}

.stage__bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  transition: opacity 600ms ease;
}

.stage__bg--ready {
  opacity: 1;
}

.stage__bg--leaving {
  opacity: 0;
  transition: opacity 400ms ease;
}

.center-box {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 24rpx;
}

.loading-dot {
  width: 56rpx;
  height: 56rpx;
  border-radius: 50%;
  border: 4rpx solid rgba(255, 255, 255, 0.25);
  border-top-color: rgba(255, 255, 255, 0.9);
  animation: spin 900ms linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.loading-text {
  color: rgba(255, 255, 255, 0.85);
  font-size: 26rpx;
  letter-spacing: 2rpx;
}

.error-text {
  color: rgba(255, 255, 255, 0.92);
  font-size: 28rpx;
  padding: 0 80rpx;
  text-align: center;
  line-height: 1.6;
}

.retry-btn {
  margin-top: 12rpx;
  font-size: 28rpx;
  padding: 8rpx 48rpx;
  border-radius: 999rpx;
  color: #0b1220;
  background: rgba(255, 255, 255, 0.92);
}

.badge {
  position: absolute;
  top: calc(env(safe-area-inset-top) + 40rpx);
  left: 32rpx;
  padding: 10rpx 24rpx;
  border-radius: 999rpx;
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(12px);
  color: rgba(255, 255, 255, 0.92);
  font-size: 24rpx;
  letter-spacing: 1rpx;
}

.glass-card {
  position: absolute;
  left: 48rpx;
  right: 48rpx;
  top: 50%;
  transform: translateY(-46%) scale(0.98);
  padding: 44rpx 40rpx;
  border-radius: 32rpx;
  opacity: 0;
  transition: opacity 700ms ease, transform 700ms ease;
  display: flex;
  flex-direction: column;
  gap: 18rpx;
}

.glass-card--visible {
  opacity: 1;
  transform: translateY(-50%) scale(1);
}

.glass-card--dark {
  background: rgba(10, 14, 24, 0.44);
  border: 1rpx solid rgba(255, 255, 255, 0.14);
  backdrop-filter: blur(24px) saturate(1.2);
}

.glass-card--dark .glass-card__text {
  color: #ffffff;
}

.glass-card--dark .glass-card__category,
.glass-card--dark .glass-card__source {
  color: rgba(255, 255, 255, 0.68);
}

.glass-card--light {
  background: rgba(255, 255, 255, 0.52);
  border: 1rpx solid rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(24px) saturate(1.2);
}

.glass-card--light .glass-card__text {
  color: #16213a;
}

.glass-card--light .glass-card__category,
.glass-card--light .glass-card__source {
  color: rgba(22, 33, 58, 0.62);
}

.glass-card__category {
  font-size: 24rpx;
  letter-spacing: 4rpx;
}

.glass-card__text {
  font-size: 34rpx;
  line-height: 1.75;
  letter-spacing: 1rpx;
}

.glass-card__source {
  font-size: 22rpx;
}

.glass-card__actions {
  display: flex;
  gap: 20rpx;
  margin-top: 8rpx;
}

.feedback-btn {
  min-height: 80rpx;
  display: flex;
  align-items: center;
  padding: 0 30rpx;
  border-radius: 999rpx;
  background: rgba(127, 127, 127, 0.18);
  font-size: 24rpx;
  color: inherit;
}

.feedback-btn--active {
  background: rgba(64, 158, 255, 0.35);
}

.timer {
  position: absolute;
  left: 48rpx;
  right: 48rpx;
  bottom: calc(env(safe-area-inset-bottom) + 64rpx);
  height: 6rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.22);
  overflow: hidden;
}

.timer__fill {
  height: 100%;
  width: 100%;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.85);
  transform-origin: left center;
  animation-name: timer-run;
  animation-timing-function: linear;
  animation-fill-mode: forwards;
}

@keyframes timer-run {
  from {
    transform: scaleX(1);
  }

  to {
    transform: scaleX(0);
  }
}

.skip-hint {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(env(safe-area-inset-bottom) + 20rpx);
  text-align: center;
  font-size: 20rpx;
  color: rgba(255, 255, 255, 0.55);
}

.fade-out {
  opacity: 0 !important;
  transition: opacity 380ms ease;
}
</style>
