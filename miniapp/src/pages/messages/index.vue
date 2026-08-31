<script setup lang="ts">
/**
 * 消息中心（M1 第一片）：平台通知只读列表 + 已读操作。
 *
 * 零 mp 专属后端——直调既有 /api/message-center/{summary,items,read}
 * （bearer 直通）。私信收发留在 Web，此处只聚合通知类消息。
 */
import { onPullDownRefresh, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { request } from "../../utils/api";
import { relativeTimeLabel } from "../../utils/format";

interface MessageItem {
  id: number;
  title: string;
  body_preview: string;
  category: string;
  category_label: string;
  severity: string;
  severity_label: string;
  created_at: string;
  is_unread: boolean;
  link_url?: string;
}

const items = ref<MessageItem[]>([]);
const loading = ref(true);
const failed = ref(false);
const tab = ref<"all" | "unread">("all");
const marking = ref(false);

const unreadCount = computed(() => items.value.filter((item) => item.is_unread).length);
const visibleItems = computed(() =>
  tab.value === "unread" ? items.value.filter((item) => item.is_unread) : items.value,
);

async function loadItems(): Promise<void> {
  loading.value = true;
  failed.value = false;
  try {
    const data = await request<{ items: MessageItem[] }>({
      path: "/api/message-center/items?limit=150",
    });
    items.value = data.items ?? [];
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

async function openItem(item: MessageItem): Promise<void> {
  if (item.is_unread) {
    item.is_unread = false;
    try {
      await request({
        path: "/api/message-center/read",
        method: "POST",
        data: { notification_ids: [item.id] },
      });
    } catch {
      /* 标记失败不打断阅读，下次刷新校正 */
    }
  }
  // 作业/考试相关通知深链到作答页；其余仅展开阅读（正文即预览）
  const match = /\/(?:assignment|exam\/take)\/(\d+)/.exec(item.link_url || "");
  if (match) {
    uni.navigateTo({ url: `/pages/task-detail/index?id=${match[1]}` });
  }
}

async function markAllRead(): Promise<void> {
  if (marking.value || !unreadCount.value) return;
  marking.value = true;
  try {
    await request({
      path: "/api/message-center/read",
      method: "POST",
      data: { category: "all" },
    });
    items.value = items.value.map((item) => ({ ...item, is_unread: false }));
    uni.showToast({ title: "已全部标记已读", icon: "success" });
  } catch {
    uni.showToast({ title: "操作失败，请重试", icon: "none" });
  } finally {
    marking.value = false;
  }
}

onShow(() => {
  void loadItems();
});

onPullDownRefresh(() => {
  void loadItems();
});
</script>

<template>
  <view class="messages">
    <view class="toolbar">
      <view class="tabs glass-chip">
        <view
          class="tabs__item press"
          :class="{ 'tabs__item--active': tab === 'all' }"
          @tap="tab = 'all'"
        >
          <text>全部</text>
        </view>
        <view
          class="tabs__item press"
          :class="{ 'tabs__item--active': tab === 'unread' }"
          @tap="tab = 'unread'"
        >
          <text>未读{{ unreadCount ? ` ${unreadCount}` : "" }}</text>
        </view>
      </view>
      <text v-if="unreadCount" class="toolbar__action press" @tap="markAllRead">全部已读</text>
    </view>

    <view v-if="loading && !items.length" class="empty"><text>加载中…</text></view>
    <view v-else-if="failed" class="empty" @tap="loadItems"><text>加载失败，点击重试</text></view>
    <view v-else-if="!visibleItems.length" class="empty">
      <text>{{ tab === "unread" ? "🎉 没有未读消息" : "还没有任何消息" }}</text>
    </view>

    <view
      v-for="item in visibleItems"
      :key="item.id"
      class="glass-card message press"
      :class="{ 'message--read': !item.is_unread }"
      @tap="openItem(item)"
    >
      <view class="message__head">
        <view class="message__title-row">
          <view v-if="item.is_unread" class="message__dot" />
          <text class="message__title">{{ item.title }}</text>
        </view>
        <text class="message__time">{{ relativeTimeLabel(item.created_at) }}</text>
      </view>
      <text v-if="item.body_preview" class="message__preview">{{ item.body_preview }}</text>
      <view class="message__meta">
        <text class="message__chip">{{ item.category_label }}</text>
        <text v-if="item.severity === 'important'" class="message__chip message__chip--important">
          {{ item.severity_label }}
        </text>
      </view>
    </view>
  </view>
</template>

<style scoped>
.messages {
  min-height: 100vh;
  padding: 28rpx 30rpx calc(env(safe-area-inset-bottom) + 32rpx);
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tabs {
  display: flex;
  padding: 6rpx;
  gap: 4rpx;
}

.tabs__item {
  padding: 12rpx 32rpx;
  border-radius: 999rpx;
  font-size: 25rpx;
  color: #66718f;
}

.tabs__item--active {
  background: linear-gradient(135deg, #5b8cff 0%, #4a7dff 100%);
  color: #ffffff;
  font-weight: 600;
}

.toolbar__action {
  font-size: 25rpx;
  font-weight: 600;
  color: #2f5ee0;
  padding: 8rpx 12rpx;
}

.empty {
  padding: 120rpx 40rpx;
  text-align: center;
  color: #8b96b3;
  font-size: 27rpx;
}

.message {
  padding: 26rpx 30rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.message--read {
  opacity: 0.62;
}

.message__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16rpx;
}

.message__title-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
  min-width: 0;
  flex: 1;
}

.message__dot {
  width: 14rpx;
  height: 14rpx;
  border-radius: 999rpx;
  background: #e5484d;
  flex-shrink: 0;
}

.message__title {
  font-size: 28rpx;
  font-weight: 650;
  color: #1b2540;
  line-height: 1.5;
}

.message__time {
  font-size: 22rpx;
  color: #9aa6bf;
  flex-shrink: 0;
  padding-top: 4rpx;
}

.message__preview {
  font-size: 25rpx;
  color: #66718f;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.message__meta {
  display: flex;
  gap: 10rpx;
}

.message__chip {
  font-size: 20rpx;
  color: #66718f;
  background: rgba(120, 140, 200, 0.12);
  border-radius: 999rpx;
  padding: 4rpx 16rpx;
}

.message__chip--important {
  color: #d05a1f;
  background: rgba(224, 102, 47, 0.13);
  font-weight: 600;
}
</style>
