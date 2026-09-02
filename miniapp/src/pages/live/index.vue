<script setup lang="ts">
/**
 * 课堂现场（M2）：单个课堂的投票 + 随堂互动 + 举手/求助。
 *
 * 全部直调既有端点（bearer 直通，零 mp 后端）：
 * - GET  /api/polls/classrooms/{oid}/snapshot                   投票快照
 * - POST /api/polls/{id}/vote {option_ids}                      投票
 * - POST /api/polls/{id}/status {status}                        教师开始/结束
 * - GET  /api/classroom-interactions/classrooms/{oid}/snapshot  互动快照
 * - POST /api/classroom-interactions/classrooms/{oid}/activities 教师发起随堂测/提问
 * - POST /api/classroom-interactions/activities/{id}/respond    学生答题
 * - POST /api/classroom-interactions/activities/{id}/questions  学生提问
 * - POST /api/classroom-interactions/activities/{id}/close      教师结束
 * - POST /api/classroom-interactions/questions/{id}/resolve     教师标记已解答
 * - POST /api/classroom-interactions/classrooms/{oid}/signals   学生举手/求助/清除
 * 前台每 8s 轮询两份快照。
 */
import { onHide, onLoad, onPullDownRefresh, onShow, onUnload } from "@dcloudio/uni-app";
import { computed, reactive, ref } from "vue";

import { request } from "../../utils/api";
import { useAuthStore } from "../../stores/auth";

interface PollOption {
  id: number;
  label: string;
  selected: boolean;
  count?: number;
  percent?: number;
}

interface Poll {
  id: number;
  title: string;
  description: string;
  vote_type: "single" | "multiple";
  status: string;
  effective_status: string;
  options: PollOption[];
  total_voters: number;
  has_voted: boolean;
  my_option_ids: number[];
  show_results: boolean;
  can_vote: boolean;
  can_manage: boolean;
  is_mine: boolean;
}

interface ActivityOption {
  id: number;
  label: string;
  selected: boolean;
  is_correct?: boolean;
  response_count?: number;
  response_percent?: number;
}

interface LiveQuestion {
  id: number;
  question_text: string;
  display_name: string;
  status: string;
  is_mine: boolean;
  can_resolve: boolean;
}

interface Activity {
  id: number;
  kind: "quiz" | "qna";
  kind_label: string;
  title: string;
  prompt: string;
  status: string;
  response_count: number;
  open_question_count: number;
  can_close: boolean;
  can_respond: boolean;
  can_ask: boolean;
  has_responded: boolean;
  can_show_results: boolean;
  options: ActivityOption[];
  questions: LiveQuestion[];
  leaderboard?: Array<{ display_name: string }>;
}

interface Signal {
  id: number;
  display_name: string;
  signal_type: string;
  signal_label: string;
  message: string;
}

interface InteractionSnapshot {
  active_activities: Activity[];
  recent_activities: Activity[];
  signals: Signal[];
  my_signal: Signal | null;
  signal_options: Array<{ key: string; label: string }>;
}

const REFRESH_MS = 8000;
const SIGNAL_ICONS: Record<string, string> = { hand: "✋", help: "🆘", slow: "🐢", done: "✅" };

const auth = useAuthStore();
const offeringId = ref(0);
const polls = ref<Poll[]>([]);
const interaction = ref<InteractionSnapshot | null>(null);
const loading = ref(true);
const busy = ref(false);
const pollChoice = reactive<Record<number, number[]>>({});
const questionDraft = reactive<Record<number, string>>({});
let timer: ReturnType<typeof setInterval> | null = null;

// 教师发起随堂测/提问表单
const composerOpen = ref(false);
const composer = reactive({
  kind: "quiz" as "quiz" | "qna",
  prompt: "",
  options: ["", "", "", ""],
  correct: 0,
});

const isTeacher = computed(() => auth.user?.role === "teacher");
const activePolls = computed(() => polls.value.filter((p) => p.effective_status === "active"));
const draftPolls = computed(() => polls.value.filter((p) => p.status === "draft" && p.can_manage));
const activities = computed(() => interaction.value?.active_activities ?? []);
const recentActivities = computed(() => (interaction.value?.recent_activities ?? []).slice(0, 3));
const hasAnythingLive = computed(() => activePolls.value.length + activities.value.length > 0);

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

async function loadAll(silent = false): Promise<void> {
  if (!offeringId.value) return;
  if (!silent) loading.value = true;
  try {
    const [pollData, liveData] = await Promise.all([
      request<{ snapshot: Record<string, unknown> }>({
        path: `/api/polls/classrooms/${offeringId.value}/snapshot`,
      }),
      request<{ snapshot: InteractionSnapshot }>({
        path: `/api/classroom-interactions/classrooms/${offeringId.value}/snapshot`,
      }),
    ]);
    const snapshot = pollData.snapshot;
    const list = (snapshot.polls ?? snapshot.cards ?? snapshot.all_polls ?? []) as Poll[];
    polls.value = list;
    for (const poll of list) {
      if (!pollChoice[poll.id] && poll.my_option_ids?.length) {
        pollChoice[poll.id] = [...poll.my_option_ids];
      }
    }
    interaction.value = liveData.snapshot;
  } catch (error: unknown) {
    if ((error as { statusCode?: number }).statusCode === 401) {
      uni.reLaunch({ url: "/pages/welcome/index" });
      return;
    }
    if (!silent) uni.showToast({ title: errorMessage(error, "加载失败"), icon: "none" });
  } finally {
    loading.value = false;
    uni.stopPullDownRefresh();
  }
}

// ---------- 投票 ----------

function togglePollOption(poll: Poll, optionId: number): void {
  if (!poll.can_vote) return;
  const current = pollChoice[poll.id] ?? [];
  if (poll.vote_type === "single") {
    pollChoice[poll.id] = [optionId];
    return;
  }
  pollChoice[poll.id] = current.includes(optionId)
    ? current.filter((id) => id !== optionId)
    : [...current, optionId];
}

function isChosen(poll: Poll, optionId: number): boolean {
  return (pollChoice[poll.id] ?? []).includes(optionId);
}

async function submitVote(poll: Poll): Promise<void> {
  const chosen = pollChoice[poll.id] ?? [];
  if (!chosen.length) {
    uni.showToast({ title: "请先选择选项", icon: "none" });
    return;
  }
  await act(`/api/polls/${poll.id}/vote`, { option_ids: chosen }, "投票已提交");
}

async function setPollStatus(poll: Poll, status: "active" | "closed"): Promise<void> {
  const verb = status === "active" ? "开始" : "结束";
  if (!(await confirm(`${verb}投票`, `确定${verb}「${poll.title}」？`))) return;
  await act(`/api/polls/${poll.id}/status`, { status }, `投票已${verb}`);
}

// ---------- 互动 ----------

async function respondQuiz(activity: Activity, optionId: number): Promise<void> {
  if (!activity.can_respond) return;
  await act(`/api/classroom-interactions/activities/${activity.id}/respond`, { option_id: optionId }, "已作答");
}

async function askQuestion(activity: Activity): Promise<void> {
  const text = (questionDraft[activity.id] ?? "").trim();
  if (!text) {
    uni.showToast({ title: "请输入问题", icon: "none" });
    return;
  }
  const ok = await act(
    `/api/classroom-interactions/activities/${activity.id}/questions`,
    { question_text: text, is_anonymous: true },
    "问题已提交",
  );
  if (ok) questionDraft[activity.id] = "";
}

async function closeActivity(activity: Activity): Promise<void> {
  if (!(await confirm("结束互动", `确定结束「${activity.title}」？`))) return;
  await act(`/api/classroom-interactions/activities/${activity.id}/close`, {}, "互动已结束");
}

async function resolveQuestion(question: LiveQuestion): Promise<void> {
  await act(`/api/classroom-interactions/questions/${question.id}/resolve`, {}, "已标记解答");
}

async function setSignal(signalType: string): Promise<void> {
  const mine = interaction.value?.my_signal?.signal_type;
  const next = mine === signalType ? "clear" : signalType;
  await act(
    `/api/classroom-interactions/classrooms/${offeringId.value}/signals`,
    { signal_type: next },
    next === "clear" ? "已取消" : "老师已收到",
  );
}

async function submitComposer(): Promise<void> {
  const prompt = composer.prompt.trim();
  if (!prompt) {
    uni.showToast({ title: "请输入题目/问题", icon: "none" });
    return;
  }
  const payload: Record<string, unknown> = { kind: composer.kind, prompt };
  if (composer.kind === "quiz") {
    const options = composer.options.map((label) => label.trim()).filter(Boolean);
    if (options.length < 2) {
      uni.showToast({ title: "至少两个选项", icon: "none" });
      return;
    }
    payload.options = options.map((label, index) => ({ label, is_correct: index === composer.correct }));
  }
  const ok = await act(
    `/api/classroom-interactions/classrooms/${offeringId.value}/activities`,
    payload,
    "已发起",
  );
  if (ok) {
    composerOpen.value = false;
    composer.prompt = "";
    composer.options = ["", "", "", ""];
    composer.correct = 0;
  }
}

// ---------- 公共 ----------

function confirm(titleText: string, content: string): Promise<boolean> {
  return new Promise((resolve) => {
    uni.showModal({
      title: titleText,
      content,
      success: (res) => resolve(Boolean(res.confirm)),
      fail: () => resolve(false),
    });
  });
}

async function act(path: string, data: Record<string, unknown>, successText: string): Promise<boolean> {
  if (busy.value) return false;
  busy.value = true;
  try {
    await request({ path, method: "POST", data });
    uni.showToast({ title: successText, icon: "success" });
    await loadAll(true);
    return true;
  } catch (error: unknown) {
    uni.showToast({ title: errorMessage(error, "操作失败"), icon: "none" });
    return false;
  } finally {
    busy.value = false;
  }
}

function startPolling(): void {
  if (timer) clearInterval(timer);
  timer = setInterval(() => void loadAll(true), REFRESH_MS);
}

function stopPolling(): void {
  if (timer) clearInterval(timer);
  timer = null;
}

onLoad((query) => {
  const params = (query ?? {}) as Record<string, string>;
  offeringId.value = Number(params.oid || 0);
  if (params.title) {
    uni.setNavigationBarTitle({ title: decodeURIComponent(params.title) });
  }
});

onShow(() => {
  void loadAll();
  startPolling();
});
onHide(stopPolling);
onUnload(stopPolling);
onPullDownRefresh(() => void loadAll(true));
</script>

<template>
  <view class="live">
    <!-- 学生：举手/求助 -->
    <view v-if="!isTeacher && interaction" class="glass-card signals">
      <text class="card__title">我的状态</text>
      <view class="signal-row">
        <view
          v-for="opt in interaction.signal_options"
          :key="opt.key"
          class="signal-chip press"
          :class="{ 'signal-chip--on': interaction.my_signal?.signal_type === opt.key }"
          @tap="setSignal(opt.key)"
        >
          <text>{{ SIGNAL_ICONS[opt.key] || "•" }} {{ opt.label }}</text>
        </view>
      </view>
    </view>

    <!-- 教师：举手/求助名单 + 发起 -->
    <template v-if="isTeacher">
      <view v-if="interaction?.signals.length" class="glass-card signals">
        <text class="card__title">举手 / 求助（{{ interaction.signals.length }}）</text>
        <view class="signal-list">
          <text v-for="s in interaction.signals" :key="s.id" class="signal-item">
            {{ SIGNAL_ICONS[s.signal_type] || "•" }} {{ s.display_name }} · {{ s.signal_label }}<template v-if="s.message">：{{ s.message }}</template>
          </text>
        </view>
      </view>
      <view class="composer-toggle glass-btn-primary press" @tap="composerOpen = !composerOpen">
        <text>{{ composerOpen ? "收起" : "⚡ 发起随堂测 / 提问" }}</text>
      </view>
      <view v-if="composerOpen" class="glass-card composer">
        <view class="segment glass-chip">
          <view class="segment__item" :class="{ 'segment__item--active': composer.kind === 'quiz' }" @tap="composer.kind = 'quiz'"><text>随堂测</text></view>
          <view class="segment__item" :class="{ 'segment__item--active': composer.kind === 'qna' }" @tap="composer.kind = 'qna'"><text>匿名提问</text></view>
        </view>
        <textarea v-model="composer.prompt" class="composer__prompt" :placeholder="composer.kind === 'quiz' ? '题目' : '想让大家提问的主题'" auto-height :maxlength="500" />
        <template v-if="composer.kind === 'quiz'">
          <view v-for="(_, i) in composer.options" :key="i" class="composer__opt">
            <view class="composer__correct press" :class="{ 'composer__correct--on': composer.correct === i }" @tap="composer.correct = i"><text>{{ composer.correct === i ? "✓" : String.fromCharCode(65 + i) }}</text></view>
            <input v-model="composer.options[i]" class="composer__input" :placeholder="`选项 ${String.fromCharCode(65 + i)}`" />
          </view>
          <text class="composer__hint">点字母标记正确答案</text>
        </template>
        <button class="glass-btn-primary composer__submit" :disabled="busy" @tap="submitComposer">发起</button>
      </view>
    </template>

    <view v-if="loading && !interaction" class="empty"><text>加载中…</text></view>
    <view v-else-if="!hasAnythingLive && !draftPolls.length" class="empty">
      <text>当前没有进行中的投票或互动</text>
    </view>

    <!-- 投票 -->
    <view v-for="poll in [...activePolls, ...draftPolls]" :key="`p${poll.id}`" class="glass-card block">
      <view class="block__head">
        <text class="block__tag block__tag--poll">投票{{ poll.vote_type === 'multiple' ? '·多选' : '' }}</text>
        <text v-if="poll.status === 'draft'" class="block__tag">草稿</text>
        <text class="block__meta">{{ poll.total_voters }} 人已投</text>
      </view>
      <text class="block__title">{{ poll.title }}</text>
      <text v-if="poll.description" class="block__desc">{{ poll.description }}</text>
      <view class="options">
        <view
          v-for="opt in poll.options"
          :key="opt.id"
          class="option press"
          :class="{ 'option--chosen': isChosen(poll, opt.id), 'option--locked': !poll.can_vote }"
          @tap="togglePollOption(poll, opt.id)"
        >
          <view v-if="poll.show_results" class="option__bar" :style="{ width: `${opt.percent ?? 0}%` }" />
          <text class="option__label">{{ opt.label }}</text>
          <text v-if="poll.show_results" class="option__count">{{ opt.count ?? 0 }} · {{ opt.percent ?? 0 }}%</text>
          <text v-else-if="opt.selected" class="option__count">已选</text>
        </view>
      </view>
      <view class="block__actions">
        <button v-if="poll.can_vote" class="glass-btn-primary act-btn" :disabled="busy" @tap="submitVote(poll)">
          {{ poll.has_voted ? "修改投票" : "提交投票" }}
        </button>
        <text v-else-if="!isTeacher && poll.has_voted" class="block__done">✓ 已投票</text>
        <button v-if="poll.can_manage && poll.status === 'draft'" class="act-btn act-btn--plain" :disabled="busy" @tap="setPollStatus(poll, 'active')">开始</button>
        <button v-if="poll.can_manage && poll.effective_status === 'active'" class="act-btn act-btn--danger" :disabled="busy" @tap="setPollStatus(poll, 'closed')">结束</button>
      </view>
    </view>

    <!-- 互动 -->
    <view v-for="a in activities" :key="`a${a.id}`" class="glass-card block">
      <view class="block__head">
        <text class="block__tag block__tag--quiz">{{ a.kind_label }}</text>
        <text class="block__meta">{{ a.kind === 'quiz' ? `${a.response_count} 人已答` : `${a.questions.length} 个问题` }}</text>
      </view>
      <text class="block__title">{{ a.prompt || a.title }}</text>

      <view v-if="a.kind === 'quiz'" class="options">
        <view
          v-for="opt in a.options"
          :key="opt.id"
          class="option press"
          :class="{ 'option--chosen': opt.selected, 'option--locked': !a.can_respond, 'option--correct': opt.is_correct }"
          @tap="respondQuiz(a, opt.id)"
        >
          <view v-if="a.can_show_results" class="option__bar" :style="{ width: `${opt.response_percent ?? 0}%` }" />
          <text class="option__label">{{ opt.label }}</text>
          <text v-if="a.can_show_results" class="option__count">{{ opt.response_count ?? 0 }} · {{ opt.response_percent ?? 0 }}%</text>
          <text v-else-if="opt.selected" class="option__count">已选</text>
        </view>
      </view>

      <template v-else>
        <view v-if="a.can_ask" class="ask">
          <input v-model="questionDraft[a.id]" class="ask__input" placeholder="匿名提问…" :maxlength="500" />
          <button class="glass-btn-primary ask__btn" :disabled="busy" @tap="askQuestion(a)">提问</button>
        </view>
        <view v-for="q in a.questions" :key="q.id" class="question" :class="{ 'question--resolved': q.status !== 'open' }">
          <text class="question__text">{{ q.question_text }}</text>
          <view class="question__foot">
            <text class="question__who">{{ q.display_name }}{{ q.is_mine ? "（我）" : "" }}</text>
            <text v-if="q.status !== 'open'" class="question__who">已解答</text>
            <text v-else-if="q.can_resolve" class="question__resolve press" @tap="resolveQuestion(q)">标记已解答</text>
          </view>
        </view>
      </template>

      <view v-if="a.can_close" class="block__actions">
        <button class="act-btn act-btn--danger" :disabled="busy" @tap="closeActivity(a)">结束互动</button>
      </view>
    </view>

    <!-- 最近结束 -->
    <template v-if="recentActivities.length">
      <view class="section-title"><text>最近结束</text></view>
      <view v-for="a in recentActivities" :key="`r${a.id}`" class="glass-card block block--dim">
        <view class="block__head">
          <text class="block__tag">{{ a.kind_label }}</text>
          <text class="block__meta">{{ a.response_count }} 人参与</text>
        </view>
        <text class="block__title">{{ a.prompt || a.title }}</text>
        <view v-if="a.kind === 'quiz'" class="options">
          <view v-for="opt in a.options" :key="opt.id" class="option option--locked" :class="{ 'option--correct': opt.is_correct, 'option--chosen': opt.selected }">
            <view class="option__bar" :style="{ width: `${opt.response_percent ?? 0}%` }" />
            <text class="option__label">{{ opt.label }}</text>
            <text class="option__count">{{ opt.response_count ?? 0 }}</text>
          </view>
        </view>
        <text v-if="a.leaderboard?.length" class="block__desc">🏆 抢答榜：{{ a.leaderboard.map((l) => l.display_name).join("、") }}</text>
      </view>
    </template>
  </view>
</template>

<style scoped>
.live {
  min-height: 100vh;
  padding: 28rpx 30rpx calc(env(safe-area-inset-bottom) + 40rpx);
  display: flex;
  flex-direction: column;
  gap: 22rpx;
}

.empty {
  padding: 80rpx 40rpx;
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

.section-title {
  padding: 8rpx 8rpx 0;
  font-size: 26rpx;
  font-weight: 700;
  color: #66718f;
}

.signals {
  padding: 24rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.signal-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
}

.signal-chip {
  padding: 14rpx 26rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.65);
  border: 1rpx solid rgba(120, 140, 200, 0.25);
  font-size: 26rpx;
  color: #2f3d5e;
}

.signal-chip--on {
  background: linear-gradient(135deg, #5b8cff 0%, #4a7dff 100%);
  border-color: transparent;
  color: #ffffff;
  font-weight: 600;
}

.signal-list {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.signal-item {
  font-size: 26rpx;
  color: #1b2540;
}

.composer-toggle {
  min-height: 84rpx;
  border-radius: 999rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28rpx;
  font-weight: 650;
}

.composer {
  padding: 26rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
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

.composer__prompt,
.composer__input,
.ask__input {
  width: 100%;
  box-sizing: border-box;
  border: 2rpx solid rgba(120, 140, 200, 0.28);
  background: rgba(255, 255, 255, 0.8);
  border-radius: 16rpx;
  padding: 18rpx 22rpx;
  font-size: 27rpx;
  color: #1b2540;
}

.composer__prompt {
  min-height: 110rpx;
}

.composer__opt {
  display: flex;
  align-items: center;
  gap: 14rpx;
}

.composer__correct {
  width: 64rpx;
  height: 64rpx;
  border-radius: 999rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(120, 140, 200, 0.14);
  color: #2f3d5e;
  font-size: 26rpx;
  font-weight: 700;
  flex-shrink: 0;
}

.composer__correct--on {
  background: #1e9e6a;
  color: #ffffff;
}

.composer__hint {
  font-size: 22rpx;
  color: #9aa6bf;
}

.composer__submit {
  min-height: 80rpx;
  border-radius: 999rpx;
  font-size: 27rpx;
  font-weight: 650;
  margin: 0;
}

.block {
  padding: 26rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.block--dim {
  opacity: 0.7;
}

.block__head {
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.block__tag {
  font-size: 20rpx;
  color: #66718f;
  background: rgba(120, 140, 200, 0.12);
  border-radius: 999rpx;
  padding: 4rpx 16rpx;
}

.block__tag--poll {
  color: #2f5ee0;
  background: rgba(74, 125, 255, 0.13);
}

.block__tag--quiz {
  color: #b08a2e;
  background: rgba(240, 195, 90, 0.18);
}

.block__meta {
  margin-left: auto;
  font-size: 22rpx;
  color: #9aa6bf;
}

.block__title {
  font-size: 30rpx;
  font-weight: 700;
  color: #1b2540;
  line-height: 1.55;
}

.block__desc {
  font-size: 24rpx;
  color: #66718f;
  line-height: 1.6;
}

.options {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.option {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12rpx;
  padding: 20rpx 24rpx;
  border-radius: 16rpx;
  background: rgba(255, 255, 255, 0.6);
  border: 2rpx solid rgba(120, 140, 200, 0.2);
}

.option--chosen {
  border-color: #4a7dff;
  background: rgba(74, 125, 255, 0.1);
}

.option--correct {
  border-color: #1e9e6a;
}

.option--locked {
  opacity: 0.92;
}

.option__bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  background: rgba(74, 125, 255, 0.14);
  transition: width 0.3s ease;
}

.option__label {
  position: relative;
  font-size: 27rpx;
  color: #1b2540;
  flex: 1;
}

.option__count {
  position: relative;
  font-size: 22rpx;
  color: #66718f;
  flex-shrink: 0;
}

.block__actions {
  display: flex;
  gap: 12rpx;
  align-items: center;
}

.act-btn {
  flex: 1;
  min-height: 76rpx;
  border-radius: 999rpx;
  font-size: 26rpx;
  font-weight: 650;
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.act-btn--plain {
  background: rgba(255, 255, 255, 0.85);
  border: 2rpx solid rgba(120, 140, 200, 0.3);
  color: #2f3d5e;
}

.act-btn--danger {
  background: rgba(255, 255, 255, 0.85);
  border: 2rpx solid rgba(229, 72, 77, 0.3);
  color: #e5484d;
}

.block__done {
  font-size: 25rpx;
  color: #1e9e6a;
  font-weight: 600;
}

.ask {
  display: flex;
  gap: 12rpx;
  align-items: center;
}

.ask__input {
  flex: 1;
}

.ask__btn {
  flex: 0 0 140rpx;
  min-height: 72rpx;
  border-radius: 999rpx;
  font-size: 25rpx;
  margin: 0;
}

.question {
  padding: 16rpx 20rpx;
  border-radius: 14rpx;
  background: rgba(255, 255, 255, 0.55);
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.question--resolved {
  opacity: 0.55;
}

.question__text {
  font-size: 26rpx;
  color: #1b2540;
  line-height: 1.55;
}

.question__foot {
  display: flex;
  justify-content: space-between;
}

.question__who {
  font-size: 21rpx;
  color: #9aa6bf;
}

.question__resolve {
  font-size: 22rpx;
  color: #2f5ee0;
  font-weight: 600;
}
</style>
