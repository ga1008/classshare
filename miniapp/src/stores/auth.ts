/**
 * 认证 store：微信静默登录 → 已绑定发 token / 未绑定走首绑流程。
 */
import { defineStore } from "pinia";

import { request, setStoredToken, getStoredToken } from "../utils/api";

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
  }),
  getters: {
    isLoggedIn: (state) => Boolean(state.token && state.user),
    isTeacher: (state) => state.user?.role === "teacher",
  },
  actions: {
    applyLoginSuccess(data: LoginResponse) {
      this.token = data.token || "";
      this.user = data.user || null;
      this.loginTips = data.login_tip?.tips ?? [];
      this.bindTicket = "";
      setStoredToken(this.token);
    },

    /** 冷启动静默登录。返回 "success" | "need_bind"。 */
    async silentLogin(): Promise<"success" | "need_bind"> {
      const code = await wxLoginCode();
      const data = await request<LoginResponse>({
        path: "/api/mp/auth/login",
        method: "POST",
        data: { code },
        auth: false,
      });
      if (data.status === "need_bind") {
        this.bindTicket = data.bind_ticket || "";
        return "need_bind";
      }
      this.applyLoginSuccess(data);
      return "success";
    },

    async bindStudent(name: string, studentIdNumber: string): Promise<void> {
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
      this.applyLoginSuccess(data);
    },

    async bindTeacher(email: string, password: string): Promise<void> {
      const data = await request<LoginResponse>({
        path: "/api/mp/auth/bind/teacher",
        method: "POST",
        data: { bind_ticket: this.bindTicket, email, password },
        auth: false,
      });
      this.applyLoginSuccess(data);
    },

    async logout(): Promise<void> {
      try {
        await request({ path: "/api/mp/auth/logout", method: "POST" });
      } catch {
        /* 网络失败也要本地登出 */
      }
      this.token = "";
      this.user = null;
      this.loginTips = [];
      setStoredToken("");
    },
  },
});
