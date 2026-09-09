<script setup lang="ts">
/**
 * 成绩单（M3，只读投影）：直调既有 GET /api/report-card（bearer 直通）。
 * 学期概览 + 按课程折叠的逐次成绩（我的分 vs 班均 + 段位）。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, reactive, ref } from "vue";

import { request } from "../../utils/api";
import { ensurePageSession, redirectToLogin } from "../../utils/session";
import { ASSESSMENT_FILTER_OPTIONS, classroomGradeKey, type AssessmentClassification } from "../../utils/assessment";

interface RecordItem extends AssessmentClassification {
  assignment_id: number | string;
  title: string;
  kind_label: string;
  date_label: string;
  my_score: number | null;
  class_avg: number | null;
  band_label: string;
  band_tone: string;
  is_late: boolean;
  is_absence_score?: boolean;
  is_regrading?: boolean;
  score_visible?: boolean;
  grade_display_state?: string;
  course_name?: string;
}

interface CourseItem {
  course_id: number;
  class_offering_id?: number | null;
  semester_id?: number | null;
  semester_name?: string;
  course_name: string;
  records: RecordItem[];
  avg_score: number | null;
  trend_label: string;
}

interface ReportCard {
  courses: CourseItem[];
  personal_records?: RecordItem[];
  published_grades?: Array<{
    publication_id: number;
    version: number;
    course_name: string;
    semester_name: string;
    published_at: string;
    ordinary_score: number | null;
    midterm_score: number | null;
    final_exam_score: number | null;
    overall_score: number | null;
    formula: { text?: string };
  }>;
  summary: {
    record_total: number;
    course_total: number;
    overall_avg: number | null;
    top_band_count: number;
    best_course: string;
    weakest_course: string;
  };
}

const BAND_COLORS: Record<string, string> = {
  top: "#1e9e6a",
  high: "#2f5ee0",
  mid: "#d97706",
  low: "#e5484d",
  success: "#1e9e6a",
  info: "#2f5ee0",
  warning: "#d97706",
  danger: "#e5484d",
};

const card = ref<ReportCard | null>(null);
const loading = ref(true);
const failed = ref(false);
const expanded = reactive<Record<string, boolean>>({});
const categoryIndex = ref(0);
const category = computed(() => ASSESSMENT_FILTER_OPTIONS[categoryIndex.value] ?? ASSESSMENT_FILTER_OPTIONS[0]);
let loadSequence = 0;

function selectCategory(event: { detail: { value: string | number } }): void {
  categoryIndex.value = Number(event.detail.value);
  void load();
}

function recordState(record: RecordItem): string {
  if (record.is_regrading && record.my_score !== null) return "重批中 · 保留原成绩";
  if (record.is_absence_score && record.score_visible !== false) return "缺交记分";
  if (record.grade_display_state === "group_pending") return "待揭晓";
  if (record.grade_display_state === "returned") return "已退回";
  if (record.my_score === null) return "待出分";
  return "";
}

async function load(): Promise<void> {
  if (!(await ensurePageSession("student"))) return;
  const sequence = ++loadSequence;
  loading.value = true;
  failed.value = false;
  try {
    const query = category.value.value ? `?assessment_kind=${category.value.value}` : "";
    const data = await request<{ report_card: ReportCard }>({ path: `/api/report-card${query}` });
    if (sequence !== loadSequence) return;
    card.value = data.report_card;
    if (card.value.courses.length && !card.value.courses.some((course) => expanded[classroomGradeKey(course)])) {
      expanded[classroomGradeKey(card.value.courses[0])] = true;
    }
  } catch (error: unknown) {
    if (sequence !== loadSequence) return;
    failed.value = true;
    if ((error as { statusCode?: number }).statusCode === 401) {
      redirectToLogin();
    }
  } finally {
    if (sequence === loadSequence) loading.value = false;
    uni.stopPullDownRefresh();
  }
}

function toggle(course: CourseItem): void {
  const key = classroomGradeKey(course);
  expanded[key] = !expanded[key];
}

function openRecord(record: RecordItem): void {
  uni.navigateTo({ url: `/pages/task-detail/index?id=${record.assignment_id}` });
}

onShow(() => void load());
onPullDownRefresh(() => void load());
</script>

<template>
  <view class="page">
    <picker :range="ASSESSMENT_FILTER_OPTIONS" range-key="label" :value="categoryIndex" @change="selectCategory">
      <view class="glass-card category-filter"><text>{{ category.label }}</text><text>{{ loading ? "加载中…" : "切换类型 ▾" }}</text></view>
    </picker>
    <view v-if="loading" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="load"><text>加载失败，点击重试</text></view>

    <template v-else-if="card">
      <view v-if="card.published_grades?.length" class="glass-card course">
        <view class="course__head"><text class="course__name">已公布课程成绩</text></view>
        <view v-for="grade in card.published_grades" :key="grade.publication_id" class="publication">
          <view class="record__body">
            <text class="record__title">{{ grade.course_name }} · {{ grade.semester_name }}</text>
            <text class="record__meta">平时 {{ grade.ordinary_score ?? "—" }}<template v-if="grade.midterm_score !== null"> · 期中 {{ grade.midterm_score }}</template> · 期末 {{ grade.final_exam_score ?? "—" }}</text>
            <text v-if="grade.formula?.text" class="record__meta">{{ grade.formula.text }}</text>
            <text class="record__meta">第 {{ grade.version }} 版 · {{ grade.published_at.slice(0, 10) }} 公布</text>
          </view>
          <view class="record__right"><text class="record__score">{{ grade.overall_score ?? "—" }}</text><text class="record__avg">课程总评</text></view>
        </view>
      </view>
      <view class="glass-card hero">
        <view class="hero__main">
          <text class="hero__value">{{ card.summary.overall_avg ?? "—" }}</text>
          <text class="hero__label">已评分任务均分</text>
        </view>
        <text class="hero__note">任务均分仅供学习参考，正式课程成绩以教师发布为准。</text>
        <view class="hero__grid">
          <view class="hero__cell"><text class="hero__num">{{ card.summary.record_total }}</text><text class="hero__sub">已评分</text></view>
          <view class="hero__cell"><text class="hero__num">{{ card.summary.course_total }}</text><text class="hero__sub">门课程</text></view>
          <view class="hero__cell"><text class="hero__num">{{ card.summary.top_band_count }}</text><text class="hero__sub">次领先</text></view>
        </view>
        <view v-if="card.summary.best_course || card.summary.weakest_course" class="hero__notes">
          <text v-if="card.summary.best_course" class="hero__note hero__note--good">🏆 最强：{{ card.summary.best_course }}</text>
          <text v-if="card.summary.weakest_course" class="hero__note hero__note--warn">📌 待加强：{{ card.summary.weakest_course }}</text>
        </view>
      </view>

      <view v-if="!card.courses.length" class="empty"><text>该类型暂无任务成绩记录</text></view>

      <view v-for="course in card.courses" :key="classroomGradeKey(course)" class="glass-card course">
        <view class="course__head press" @tap="toggle(course)">
          <view class="course__body">
            <text class="course__name">{{ course.course_name }}</text>
            <text v-if="course.semester_name" class="course__meta">{{ course.semester_name }}</text>
            <text class="course__meta">{{ course.records.length }} 次 · 均分 {{ course.avg_score ?? "—" }}<template v-if="course.trend_label"> · {{ course.trend_label }}</template></text>
          </view>
          <text class="course__arrow">{{ expanded[classroomGradeKey(course)] ? "▴" : "▾" }}</text>
        </view>

        <view v-if="expanded[classroomGradeKey(course)]" class="records">
          <view v-for="r in course.records" :key="r.assignment_id" class="record press" @tap="openRecord(r)">
            <view class="record__body">
              <text class="record__title">{{ r.title }}</text>
              <text class="record__meta">{{ r.kind_label }} · {{ r.date_label }}<template v-if="r.is_late"> · 迟交</template></text>
              <text v-if="recordState(r)" class="record__meta">{{ recordState(r) }}</text>
            </view>
            <view class="record__right">
              <text class="record__score">{{ r.my_score ?? "—" }}</text>
              <text class="record__avg">班均 {{ r.class_avg ?? "—" }}</text>
              <text class="record__band" :style="{ color: BAND_COLORS[r.band_tone] || '#66718f' }">{{ r.band_label }}</text>
            </view>
          </view>
        </view>
      </view>
      <view v-if="card.personal_records?.length" class="glass-card course">
        <view class="course__head"><view class="course__body"><text class="course__name">个人阶段试炼</text><text class="course__meta">独立学习记录，不计入课程任务均分</text></view></view>
        <view v-for="r in card.personal_records" :key="r.assignment_id" class="record press" @tap="openRecord(r)">
          <view class="record__body"><text class="record__title">{{ r.title }}</text><text class="record__meta">{{ r.course_name }} · {{ r.date_label }}<template v-if="recordState(r)"> · {{ recordState(r) }}</template></text></view>
          <text class="record__score">{{ r.my_score ?? "—" }}</text>
        </view>
      </view>
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

.category-filter {
  display: flex;
  justify-content: space-between;
  padding: 24rpx 30rpx;
  color: #66718f;
  font-size: 25rpx;
}

.hero {
  padding: 32rpx 34rpx;
  display: flex;
  flex-direction: column;
  gap: 22rpx;
}

.hero__main {
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}

.hero__value {
  font-size: 72rpx;
  font-weight: 800;
  color: #2f5ee0;
  line-height: 1;
}

.hero__label {
  font-size: 25rpx;
  color: #66718f;
}

.hero__grid {
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
  font-size: 34rpx;
  font-weight: 700;
  color: #1b2540;
}

.hero__sub {
  font-size: 21rpx;
  color: #9aa6bf;
}

.hero__notes {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.hero__note {
  font-size: 24rpx;
}

.hero__note--good {
  color: #1e9e6a;
}

.hero__note--warn {
  color: #d97706;
}

.course {
  padding: 8rpx 30rpx;
}

.course__head {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 22rpx 0;
}

.course__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.course__name {
  font-size: 29rpx;
  font-weight: 700;
  color: #1b2540;
}

.course__meta {
  font-size: 23rpx;
  color: #8b96b3;
}

.course__arrow {
  font-size: 26rpx;
  color: #aab3c9;
}

.records {
  display: flex;
  flex-direction: column;
  padding-bottom: 12rpx;
}

.record {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 18rpx 0;
  border-top: 1rpx solid rgba(130, 148, 200, 0.14);
}

.publication {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 22rpx 0;
  border-top: 1rpx solid rgba(130, 148, 200, 0.14);
}

.record__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.record__title {
  font-size: 27rpx;
  color: #1b2540;
}

.record__meta {
  font-size: 22rpx;
  color: #9aa6bf;
}

.record__right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2rpx;
  flex-shrink: 0;
}

.record__score {
  font-size: 36rpx;
  font-weight: 800;
  color: #1b2540;
  line-height: 1.1;
}

.record__avg {
  font-size: 21rpx;
  color: #9aa6bf;
}

.record__band {
  font-size: 21rpx;
  font-weight: 600;
}
</style>
