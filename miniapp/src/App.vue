<script setup lang="ts">
import { onLaunch } from "@dcloudio/uni-app";

onLaunch(() => {
  /* 冷启动逻辑在 welcome 页处理 */
});
</script>

<style>
/**
 * 全局设计语言：简约 · 轻拟物（磨砂玻璃）。
 * 背景为柔和蓝紫渐变；卡片统一用 .glass-card（半透明白 + backdrop 模糊，
 * 不支持 backdrop-filter 的机型自动退化为实色半透明，观感仍成立）。
 */
page {
  background: linear-gradient(165deg, #e9eeff 0%, #f4f2fc 34%, #eaf4ff 72%, #f2f5fb 100%);
  font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
  color: #1b2540;
}

.glass-card {
  background: rgba(255, 255, 255, 0.62);
  backdrop-filter: blur(20px) saturate(1.4);
  -webkit-backdrop-filter: blur(20px) saturate(1.4);
  border: 1rpx solid rgba(255, 255, 255, 0.75);
  border-radius: 36rpx;
  box-shadow: 0 12rpx 40rpx rgba(80, 100, 180, 0.1);
}

.glass-chip {
  background: rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1rpx solid rgba(255, 255, 255, 0.7);
  border-radius: 999rpx;
}

.glass-btn-primary {
  background: linear-gradient(135deg, #5b8cff 0%, #4a7dff 100%);
  color: #ffffff;
  border-radius: 999rpx;
  box-shadow: 0 10rpx 28rpx rgba(74, 125, 255, 0.35);
  border: none;
}

.glass-btn-primary:active {
  transform: scale(0.98);
  opacity: 0.92;
}

.press:active {
  transform: scale(0.985);
  transition: transform 120ms ease;
}
</style>
