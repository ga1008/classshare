<script setup lang="ts">
/**
 * 错题本（M3，只读投影）：直调既有 GET /api/wrong-book（bearer 直通）。
 * 概览 + 知识点掌握度 + 按课程筛选的错题卡（题干/我的答案/正确答案）。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, reactive, ref } from "vue";

import { request } from "../../utils/api";

interface WrongItem {
  assignment_id: number;
  assignment_title: string;
  course_id: number;
  course_name: string;
  question_ordinal: number;
  question_type_label: string;
  question_text: string;
  options: string[];
  correct_answer: string;
  my_answer: string;
  score: number | null;
  max_score: number | null;
  knowledge_points: string[];
}

interface WrongBook {
  items: WrongItem[];
  items_truncated: boolean;
  courses: Array<{ course_id: number; course_name: string; wrong_count: number }>;
  knowledge_mastery: Array<{ name: string; total: number; wrong: number; mastery_percent: number }>;
  summary: {
    wrong_total: number;
    evaluated_total: number;
    exam_count: number;
    correct_percent: number | null;
    weakest_points: string[];
  };
}

const book = ref<WrongBook | null>(null);
const loading = ref(true);
const failed = ref(false);
const courseFilter = ref<number | null>(null);
const revealed = reactive<Record<string, boolean>>({});

const visibleItems = computed(() =>
  (book.value?.items ?? []).filter((item) => courseFilter.value === null || item.course_id === courseFilter.value),
);

function itemKey(item: WrongItem): string {
  return `${item.assignment_id}:${item.question_ordinal}`;
}

function masteryColor(percent: number): string {
  if (percent >= 85) return "#1e9e6a";
  if (percent >= 60) return "#d97706";
  return "#e5484d";
}

async function load(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    const data = await request<{ wrong_book: WrongBook }>({ path: "/api/wrong-book" });
    book.value = data.wrong_book;
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

onShow(() => void load());
onPullDownRefresh(() => void load());
</script>

<template>
  <view class="page">
    <view v-if="loading && !book" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="load"><text>加载失败，点击重试</text></view>

    <template v-else-if="book">
      <view class="glass-card hero">
        <view class="hero__row">
          <view class="hero__cell"><text class="hero__num hero__num--red">{{ book.summary.wrong_total }}</text><text class="hero__sub">错题</text></view>
          <view class="hero__cell"><text class="hero__num">{{ book.summary.correct_percent ?? "—" }}<text v-if="book.summary.correct_percent !== null" class="hero__unit">%</text></text><text class="hero__sub">客观题正确率</text></view>
          <view class="hero__cell"><text class="hero__num">{{ book.summary.exam_count }}</text><text class="hero__sub">份试卷</text></view>
        </view>
        <text v-if="book.summary.weakest_points.length" class="hero__note">📌 薄弱：{{ book.summary.weakest_points.join("、") }}</text>
      </view>

      <view v-if="book.knowledge_mastery.length" class="glass-card mastery">
        <text class="card__title">知识点掌握度</text>
        <view v-for="kp in book.knowledge_mastery.slice(0, 8)" :key="kp.name" class="kp">
          <view class="kp__head">
            <text class="kp__name">{{ kp.name }}</text>
            <text class="kp__pct" :style="{ color: masteryColor(kp.mastery_percent) }">{{ kp.mastery_percent }}%</text>
          </view>
          <view class="kp__track"><view class="kp__bar" :style="{ width: `${kp.mastery_percent}%`, background: masteryColor(kp.mastery_percent) }" /></view>
        </view>
      </view>

      <scroll-view v-if="book.courses.length > 1" class="filters" scroll-x>
        <view class="filter glass-chip press" :class="{ 'filter--on': courseFilter === null }" @tap="courseFilter = null"><text>全部 {{ book.summary.wrong_total }}</text></view>
        <view v-for="c in book.courses" :key="c.course_id" class="filter glass-chip press" :class="{ 'filter--on': courseFilter === c.course_id }" @tap="courseFilter = c.course_id"><text>{{ c.course_name }} {{ c.wrong_count }}</text></view>
      </scroll-view>

      <view v-if="!visibleItems.length" class="empty"><text>🎉 没有错题</text></view>

      <view v-for="item in visibleItems" :key="itemKey(item)" class="glass-card wrong">
        <view class="wrong__head">
          <text class="wrong__tag">{{ item.question_type_label }}</text>
          <text class="wrong__meta">{{ item.course_name }} · {{ item.assignment_title }} · 第 {{ item.question_ordinal }} 题</text>
        </view>
        <text class="wrong__text">{{ item.question_text }}</text>
        <view v-if="item.options.length" class="wrong__options">
          <text v-for="(opt, i) in item.options" :key="i" class="wrong__opt">{{ opt }}</text>
        </view>
        <view class="answer answer--mine">
          <text class="answer__label">我的答案<template v-if="item.max_score"> · {{ item.score ?? 0 }}/{{ item.max_score }}</template></text>
          <text class="answer__text">{{ item.my_answer.split("|||").join("、") }}</text>
        </view>
        <view v-if="revealed[itemKey(item)]" class="answer answer--correct">
          <text class="answer__label">正确答案</text>
          <text class="answer__text">{{ item.correct_answer || "见评语" }}</text>
        </view>
        <view v-else class="reveal press" @tap="revealed[itemKey(item)] = true"><text>👁 查看正确答案</text></view>
        <view v-if="item.knowledge_points.length" class="wrong__kps">
          <text v-for="kp in item.knowledge_points" :key="kp" class="wrong__kp">{{ kp }}</text>
        </view>
      </view>

      <text v-if="book.items_truncated" class="truncated">仅展示最近的错题，完整列表请到网页端查看</text>
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
  flex-direction: column;
  gap: 16rpx;
}

.hero__row {
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
  font-size: 40rpx;
  font-weight: 800;
  color: #1b2540;
}

.hero__num--red {
  color: #e5484d;
}

.hero__unit {
  font-size: 22rpx;
  font-weight: 600;
}

.hero__sub {
  font-size: 21rpx;
  color: #9aa6bf;
}

.hero__note {
  font-size: 24rpx;
  color: #d97706;
}

.mastery {
  padding: 26rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.kp {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.kp__head {
  display: flex;
  justify-content: space-between;
}

.kp__name {
  font-size: 25rpx;
  color: #1b2540;
}

.kp__pct {
  font-size: 23rpx;
  font-weight: 700;
}

.kp__track {
  height: 12rpx;
  border-radius: 999rpx;
  background: rgba(120, 140, 200, 0.14);
  overflow: hidden;
}

.kp__bar {
  height: 100%;
  border-radius: 999rpx;
}

.filters {
  white-space: nowrap;
}

.filter {
  display: inline-block;
  padding: 12rpx 24rpx;
  margin-right: 12rpx;
  font-size: 24rpx;
  color: #66718f;
}

.filter--on {
  background: linear-gradient(135deg, #5b8cff 0%, #4a7dff 100%);
  color: #ffffff;
  font-weight: 600;
}

.wrong {
  padding: 26rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.wrong__head {
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.wrong__tag {
  font-size: 20rpx;
  color: #b08a2e;
  background: rgba(240, 195, 90, 0.18);
  border-radius: 999rpx;
  padding: 4rpx 14rpx;
  flex-shrink: 0;
}

.wrong__meta {
  font-size: 21rpx;
  color: #9aa6bf;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wrong__text {
  font-size: 28rpx;
  color: #1b2540;
  line-height: 1.6;
  white-space: pre-wrap;
}

.wrong__options {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.wrong__opt {
  font-size: 25rpx;
  color: #334155;
  background: rgba(255, 255, 255, 0.55);
  border-radius: 12rpx;
  padding: 12rpx 18rpx;
}

.answer {
  border-radius: 16rpx;
  padding: 16rpx 22rpx;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.answer--mine {
  background: rgba(229, 72, 77, 0.07);
  border: 1rpx solid rgba(229, 72, 77, 0.18);
}

.answer--correct {
  background: rgba(30, 158, 106, 0.08);
  border: 1rpx solid rgba(30, 158, 106, 0.2);
}

.answer__label {
  font-size: 21rpx;
  font-weight: 700;
  color: #66718f;
}

.answer__text {
  font-size: 26rpx;
  color: #1b2540;
  line-height: 1.6;
}

.reveal {
  align-self: flex-start;
  font-size: 24rpx;
  color: #2f5ee0;
  font-weight: 600;
  padding: 6rpx 4rpx;
}

.wrong__kps {
  display: flex;
  flex-wrap: wrap;
  gap: 8rpx;
}

.wrong__kp {
  font-size: 20rpx;
  color: #66718f;
  background: rgba(120, 140, 200, 0.12);
  border-radius: 999rpx;
  padding: 4rpx 14rpx;
}

.truncated {
  text-align: center;
  font-size: 22rpx;
  color: #aab3c9;
  padding: 8rpx 0;
}
</style>
