<script setup lang="ts">
/**
 * 首次绑定页：学生（姓名+学号）/ 教师（邮箱+密码）。
 * 绑定成功后回欢迎屏播放"人生一言"，之后每次进入自动登录。
 */
import { ref } from "vue";

import { useAuthStore } from "../../stores/auth";

const auth = useAuthStore();
const role = ref<"student" | "teacher">("student");
const studentName = ref("");
const studentIdNumber = ref("");
const teacherEmail = ref("");
const teacherPassword = ref("");
const submitting = ref(false);

function showError(message: string): void {
  uni.showToast({ title: message, icon: "none", duration: 2600 });
}

async function submit(): Promise<void> {
  if (submitting.value) return;
  if (!auth.bindTicket) {
    showError("绑定凭证已失效，请重新进入小程序。");
    return;
  }
  submitting.value = true;
  try {
    if (role.value === "student") {
      if (!studentName.value.trim() || !studentIdNumber.value.trim()) {
        showError("请填写姓名和学号。");
        return;
      }
      await auth.bindStudent(studentName.value.trim(), studentIdNumber.value.trim());
    } else {
      if (!teacherEmail.value.trim() || !teacherPassword.value) {
        showError("请填写邮箱和密码。");
        return;
      }
      await auth.bindTeacher(teacherEmail.value.trim(), teacherPassword.value);
    }
    uni.reLaunch({ url: "/pages/welcome/index" });
  } catch (error: unknown) {
    showError(error instanceof Error ? error.message : "绑定失败，请重试。");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <view class="bind-page">
    <view class="hero">
      <text class="hero__title">欢迎来到 LanShare</text>
      <text class="hero__subtitle">首次使用请绑定你的账号，之后进入将自动登录</text>
    </view>

    <view class="role-switch">
      <view
        class="role-switch__item"
        :class="{ 'role-switch__item--active': role === 'student' }"
        @tap="role = 'student'"
      >
        <text>我是学生</text>
      </view>
      <view
        class="role-switch__item"
        :class="{ 'role-switch__item--active': role === 'teacher' }"
        @tap="role = 'teacher'"
      >
        <text>我是教师</text>
      </view>
    </view>

    <view v-if="role === 'student'" class="form">
      <view class="field">
        <text class="field__label">姓名</text>
        <input v-model="studentName" class="field__input" placeholder="请输入真实姓名" />
      </view>
      <view class="field">
        <text class="field__label">学号</text>
        <input
          v-model="studentIdNumber"
          class="field__input"
          type="number"
          placeholder="请输入学号"
        />
      </view>
      <text class="form__hint">姓名与学号需与教师录入的名册一致</text>
    </view>

    <view v-else class="form">
      <view class="field">
        <text class="field__label">邮箱</text>
        <input v-model="teacherEmail" class="field__input" placeholder="教师账号邮箱" />
      </view>
      <view class="field">
        <text class="field__label">密码</text>
        <input
          v-model="teacherPassword"
          class="field__input"
          password
          placeholder="教师账号密码"
        />
      </view>
      <text class="form__hint">教师首次绑定需验证账号密码，仅此一次</text>
    </view>

    <button class="submit-btn" :loading="submitting" @tap="submit">绑定并进入</button>
  </view>
</template>

<style scoped>
.bind-page {
  min-height: 100vh;
  background: #0b1220;
  padding: 80rpx 48rpx calc(env(safe-area-inset-bottom) + 48rpx);
  display: flex;
  flex-direction: column;
}

.hero {
  margin-bottom: 72rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.hero__title {
  color: #ffffff;
  font-size: 48rpx;
  font-weight: 600;
  letter-spacing: 2rpx;
}

.hero__subtitle {
  color: rgba(255, 255, 255, 0.55);
  font-size: 26rpx;
  line-height: 1.6;
}

.role-switch {
  display: flex;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 24rpx;
  padding: 8rpx;
  margin-bottom: 48rpx;
}

.role-switch__item {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 88rpx;
  border-radius: 18rpx;
  color: rgba(255, 255, 255, 0.6);
  font-size: 30rpx;
}

.role-switch__item--active {
  background: rgba(255, 255, 255, 0.92);
  color: #0b1220;
  font-weight: 600;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 32rpx;
  margin-bottom: 64rpx;
}

.field {
  background: rgba(255, 255, 255, 0.08);
  border-radius: 24rpx;
  padding: 24rpx 32rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.field__label {
  color: rgba(255, 255, 255, 0.5);
  font-size: 24rpx;
}

.field__input {
  color: #ffffff;
  font-size: 32rpx;
  min-height: 56rpx;
}

.form__hint {
  color: rgba(255, 255, 255, 0.38);
  font-size: 22rpx;
}

.submit-btn {
  margin-top: auto;
  min-height: 96rpx;
  border-radius: 999rpx;
  background: #4a7dff;
  color: #ffffff;
  font-size: 32rpx;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
