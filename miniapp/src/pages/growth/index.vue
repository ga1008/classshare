<script setup lang="ts">
/**
 * 修为与积分（M3，只读投影）：直调既有 GET /api/points 与 GET /api/achievements。
 * 积分余额 + 获取规则 + 明细流水 + 成就徽章墙；商城兑换留在 Web，只展示商品与可负担状态。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { ref } from "vue";

import { request } from "../../utils/api";

interface ShopItem {
  key: string;
  name?: string;
  title?: string;
  description?: string;
  cost: number;
  icon?: string;
  affordable: boolean;
}

interface EarnRule {
  kind?: string;
  label?: string;
  name?: string;
  amount?: number;
  points?: number;
}

interface PointsHome {
  balance: number;
  earn_rules: EarnRule[];
  items: ShopItem[];
  ledger: Array<{ delta: number; kind_label: string; note: string; created_at: string }>;
}

interface Badge {
  key: string;
  name: string;
  description: string;
  icon: string;
  tier_label: string;
  earned: boolean;
  earned_at: string;
  progress_hint: string;
}

interface Wall {
  badges: Badge[];
  earned_count: number;
  total_count: number;
}

const points = ref<PointsHome | null>(null);
const wall = ref<Wall | null>(null);
const loading = ref(true);
const failed = ref(false);
const tab = ref<"badges" | "ledger" | "shop">("badges");

async function load(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    const [p, w] = await Promise.all([
      request<{ points_home: PointsHome }>({ path: "/api/points" }),
      request<{ achievement_wall: Wall }>({ path: "/api/achievements" }),
    ]);
    points.value = p.points_home;
    wall.value = w.achievement_wall;
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

function ruleLabel(rule: EarnRule): string {
  return rule.label || rule.name || rule.kind || "";
}

function ruleAmount(rule: EarnRule): number {
  return Number(rule.amount ?? rule.points ?? 0);
}

onShow(() => void load());
onPullDownRefresh(() => void load());
</script>

<template>
  <view class="page">
    <view v-if="loading && !points" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="load"><text>加载失败，点击重试</text></view>

    <template v-else-if="points && wall">
      <view class="glass-card hero">
        <view class="hero__cell">
          <text class="hero__num hero__num--gold">{{ points.balance }}</text>
          <text class="hero__sub">积分余额</text>
        </view>
        <view class="hero__cell">
          <text class="hero__num">{{ wall.earned_count }}<text class="hero__unit">/{{ wall.total_count }}</text></text>
          <text class="hero__sub">成就徽章</text>
        </view>
      </view>

      <view class="segment glass-chip">
        <view class="segment__item" :class="{ 'segment__item--active': tab === 'badges' }" @tap="tab = 'badges'"><text>徽章</text></view>
        <view class="segment__item" :class="{ 'segment__item--active': tab === 'ledger' }" @tap="tab = 'ledger'"><text>积分明细</text></view>
        <view class="segment__item" :class="{ 'segment__item--active': tab === 'shop' }" @tap="tab = 'shop'"><text>商店</text></view>
      </view>

      <view v-if="tab === 'badges'" class="badges">
        <view v-for="b in wall.badges" :key="b.key" class="glass-card badge" :class="{ 'badge--locked': !b.earned }">
          <text class="badge__icon">{{ b.icon || "🏅" }}</text>
          <view class="badge__body">
            <view class="badge__head">
              <text class="badge__name">{{ b.name }}</text>
              <text class="badge__tier">{{ b.tier_label }}</text>
            </view>
            <text class="badge__desc">{{ b.description }}</text>
            <text v-if="b.earned" class="badge__hint badge__hint--done">✓ {{ b.earned_at }} 达成</text>
            <text v-else-if="b.progress_hint" class="badge__hint">{{ b.progress_hint }}</text>
          </view>
        </view>
      </view>

      <template v-if="tab === 'ledger'">
        <view class="glass-card rules">
          <text class="card__title">怎么赚积分</text>
          <view v-for="(rule, i) in points.earn_rules" :key="i" class="rule">
            <text class="rule__label">{{ ruleLabel(rule) }}</text>
            <text class="rule__amount">+{{ ruleAmount(rule) }}</text>
          </view>
        </view>
        <view class="glass-card ledger">
          <text class="card__title">最近变动</text>
          <view v-if="!points.ledger.length" class="ledger__empty"><text>还没有积分记录</text></view>
          <view v-for="(row, i) in points.ledger" :key="i" class="ledger__row">
            <view class="ledger__body">
              <text class="ledger__kind">{{ row.kind_label }}</text>
              <text class="ledger__note">{{ row.note || row.created_at }}</text>
            </view>
            <text class="ledger__delta" :class="{ 'ledger__delta--neg': row.delta < 0 }">{{ row.delta > 0 ? "+" : "" }}{{ row.delta }}</text>
          </view>
        </view>
      </template>

      <template v-if="tab === 'shop'">
        <view v-for="item in points.items" :key="item.key" class="glass-card shop" :class="{ 'shop--dim': !item.affordable }">
          <text class="shop__icon">{{ item.icon || "🎁" }}</text>
          <view class="shop__body">
            <text class="shop__name">{{ item.name || item.title }}</text>
            <text v-if="item.description" class="shop__desc">{{ item.description }}</text>
          </view>
          <view class="shop__cost">
            <text class="shop__cost-num">{{ item.cost }}</text>
            <text class="shop__cost-sub">{{ item.affordable ? "可兑换" : "积分不足" }}</text>
          </view>
        </view>
        <text class="truncated">兑换请到网页端「积分商城」操作</text>
      </template>
    </template>
  </view>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 28rpx 30rpx calc(env(safe-area-inset-bottom) + 32rpx);
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.empty {
  padding: 100rpx 40rpx;
  text-align: center;
  color: #8b96b3;
  font-size: 27rpx;
}

.card__title {
  font-size: 25rpx;
  font-weight: 700;
  color: #66718f;
  letter-spacing: 2rpx;
}

.hero {
  padding: 30rpx 34rpx;
  display: flex;
  gap: 12rpx;
}

.hero__cell {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4rpx;
  padding: 16rpx 0;
  border-radius: 16rpx;
  background: rgba(255, 255, 255, 0.55);
}

.hero__num {
  font-size: 52rpx;
  font-weight: 800;
  color: #1b2540;
  line-height: 1.1;
}

.hero__num--gold {
  color: #b08a2e;
}

.hero__unit {
  font-size: 24rpx;
  font-weight: 600;
  color: #9aa6bf;
}

.hero__sub {
  font-size: 21rpx;
  color: #9aa6bf;
}

.segment {
  display: flex;
  padding: 6rpx;
}

.segment__item {
  flex: 1;
  text-align: center;
  padding: 12rpx 0;
  border-radius: 999rpx;
  font-size: 25rpx;
  color: #66718f;
}

.segment__item--active {
  background: linear-gradient(135deg, #5b8cff 0%, #4a7dff 100%);
  color: #ffffff;
  font-weight: 600;
}

.badges {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.badge {
  padding: 22rpx 28rpx;
  display: flex;
  align-items: center;
  gap: 20rpx;
}

.badge--locked {
  opacity: 0.55;
}

.badge__icon {
  font-size: 52rpx;
  flex-shrink: 0;
}

.badge__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.badge__head {
  display: flex;
  align-items: center;
  gap: 10rpx;
}

.badge__name {
  font-size: 28rpx;
  font-weight: 700;
  color: #1b2540;
}

.badge__tier {
  font-size: 19rpx;
  color: #b08a2e;
  background: rgba(240, 195, 90, 0.18);
  border-radius: 999rpx;
  padding: 2rpx 12rpx;
}

.badge__desc {
  font-size: 23rpx;
  color: #66718f;
}

.badge__hint {
  font-size: 21rpx;
  color: #9aa6bf;
}

.badge__hint--done {
  color: #1e9e6a;
}

.rules,
.ledger {
  padding: 24rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.rule,
.ledger__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10rpx 0;
  border-top: 1rpx solid rgba(130, 148, 200, 0.12);
}

.rule__label,
.ledger__kind {
  font-size: 26rpx;
  color: #1b2540;
}

.rule__amount {
  font-size: 26rpx;
  font-weight: 700;
  color: #1e9e6a;
}

.ledger__body {
  display: flex;
  flex-direction: column;
  gap: 2rpx;
  min-width: 0;
}

.ledger__note {
  font-size: 21rpx;
  color: #9aa6bf;
}

.ledger__delta {
  font-size: 28rpx;
  font-weight: 700;
  color: #1e9e6a;
}

.ledger__delta--neg {
  color: #e5484d;
}

.ledger__empty {
  font-size: 24rpx;
  color: #b0b9cf;
  padding: 12rpx 0;
}

.shop {
  padding: 22rpx 28rpx;
  display: flex;
  align-items: center;
  gap: 18rpx;
}

.shop--dim {
  opacity: 0.6;
}

.shop__icon {
  font-size: 48rpx;
  flex-shrink: 0;
}

.shop__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.shop__name {
  font-size: 27rpx;
  font-weight: 700;
  color: #1b2540;
}

.shop__desc {
  font-size: 22rpx;
  color: #66718f;
}

.shop__cost {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  flex-shrink: 0;
}

.shop__cost-num {
  font-size: 30rpx;
  font-weight: 800;
  color: #b08a2e;
}

.shop__cost-sub {
  font-size: 20rpx;
  color: #9aa6bf;
}

.truncated {
  text-align: center;
  font-size: 22rpx;
  color: #aab3c9;
  padding: 8rpx 0;
}
</style>
