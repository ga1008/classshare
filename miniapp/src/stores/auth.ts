/**
 * 认证 store：微信静默登录 → 已绑定发 token / 未绑定走首绑流程。
 */
import { defineStore } from "pinia";

import { ApiError, request, setStoredToken, getStoredToken } from "../utils/api";

type LoginResult = "success" | "need_bind";
const loginRequests = new WeakMap<object, Promise<LoginResult>>();

export interface MpUser {
  id: number;
  name: string;
  role: "student" | "teacher";
  student_id_number?: string;
  class_name?: string;
  school_code?: string;
  department?: string;
  email?: string;
}

export interface LifeTip {
  id: number;
  category: string;
  text: string;
  source_ref?: string;
  image_url?: string | null;
}

interface LoginResponse {
  status: "success" | "need_bind";
  token?: string;
  user?: MpUser;
  login_tip?: { tips: LifeTip[] } | null;
  bind_ticket?: string;
}

function wxLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    uni.login({
      provider: "weixin",
      success: (res) => {
        if (res.code) {
          resolve(res.code);
        } else {
          reject(new Error("微信登录失败，请重试。"));
        }
      },
      fail: () => reject(new Error("微信登录失败，请重试。")),
    });
  });
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: getStoredToken(),
    user: null as MpUser | null,
    loginTips: [] as LifeTip[],
    bindTicket: "",
    generation: 0,
  }),
  getters: {
    isLoggedIn: (state) => Boolean(state.token && state.user),
    isTeacher: (state) => state.user?.role === "teacher",
  },
  actions: {
    clearSession() {
      this.generation += 1;
      loginRequests.delete(this);
      this.token = "";
      this.user = null;
      this.bindTicket = "";
      this.loginTips = [];
      setStoredToken("");
    },

    applyLoginSuccess(data: LoginResponse) {
      this.token = data.token || "";
      this.user = data.user || null;
      this.loginTips = data.login_tip?.tips ?? [];
      this.bindTicket = "";
      setStoredToken(this.token);
    },

    /** 冷启动静默登录。返回 "success" | "need_bind"。 */
    async silentLogin(): Promise<"success" | "need_bind"> {
      const existing = loginRequests.get(this);
      if (existing) return existing;
      const generation = this.generation;
      const pending = (async (): Promise<LoginResult> => {
        const code = await wxLoginCode();
        const data = await request<LoginResponse>({
          path: "/api/mp/auth/login", method: "POST", data: { code }, auth: false,
        });
        if (this.generation !== generation) throw new ApiError("登录状态已变更，请重新进入。", 409);
        if (data.status === "need_bind") {
          this.token = "";
          this.user = null;
          this.loginTips = [];
          setStoredToken("");
          this.bindTicket = data.bind_ticket || "";
          return "need_bind";
        }
        this.applyLoginSuccess(data);
        return "success";
      })();
      loginRequests.set(this, pending);
      try { return await pending; }
      finally { if (loginRequests.get(this) === pending) loginRequests.delete(this); }
    },

    async ensureSession(): Promise<LoginResult> {
      if (this.isLoggedIn && this.token === getStoredToken()) return "success";
      if (this.bindTicket && !this.token) return "need_bind";
      return this.silentLogin();
    },

    async bindStudent(name: string, studentIdNumber: string): Promise<void> {
      const generation = this.generation;
      const data = await request<LoginResponse>({
        path: "/api/mp/auth/bind/student",
        method: "POST",
        data: {
          bind_ticket: this.bindTicket,
          name,
          student_id_number: studentIdNumber,
        },
        auth: false,
      });
      if (generation !== this.generation) throw new ApiError("绑定状态已变更，请重新进入。", 409);
      this.applyLoginSuccess(data);
    },

    async bindTeacher(email: string, password: string): Promise<void> {
      const generation = this.generation;
      const data = await request<LoginResponse>({
        path: "/api/mp/auth/bind/teacher",
        method: "POST",
        data: { bind_ticket: this.bindTicket, email, password },
        auth: false,
      });
      if (generation !== this.generation) throw new ApiError("绑定状态已变更，请重新进入。", 409);
      this.applyLoginSuccess(data);
    },

    async logout(): Promise<void> {
      try {
        await request({ path: "/api/mp/auth/logout", method: "POST", redirectOnUnauthorized: false });
      } catch (error: unknown) {
        if (!(error instanceof ApiError) || error.code !== "mp_logout_session_expired") throw error;
        // Re-prove the current WeChat identity before unbinding an expired session.
        const result = await this.silentLogin();
        if (result === "success") {
          await request({ path: "/api/mp/auth/logout", method: "POST", redirectOnUnauthorized: false });
        }
      }
      this.clearSession();
    },
  },
});
