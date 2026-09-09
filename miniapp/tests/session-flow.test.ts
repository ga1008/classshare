import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "../src/stores/auth";
import { request, setStoredToken, uploadFile } from "../src/utils/api";
import { downloadProtectedTempFile } from "../src/utils/preview";
import {
  clearSessionTarget, ensurePageSession, finishSessionLogin, installSessionHandling,
  rememberLaunchTarget,
} from "../src/utils/session";
import { sanitizeSessionTarget } from "../src/utils/session-target";

type Callback = { success: (data: any) => void; fail: () => void; url?: string };
const storage = new Map<string, unknown>();
let calls: Callback[];
let uploads: Callback[];
let downloads: Callback[];
let pages: { route: string; options?: Record<string, unknown> }[];
let ui: Record<string, any>;

function loggedIn(token = "token-a", role: "student" | "teacher" = "student") {
  useAuthStore().applyLoginSuccess({ status: "success", token, user: { id: role === "teacher" ? 9 : 1, role, name: "Test" } });
}
function answer(call: Callback, statusCode: number, data: unknown) {
  call.success({ statusCode, data });
}

beforeEach(() => {
  storage.clear(); calls = []; uploads = []; downloads = [];
  pages = [{ route: "pages/task-detail/index", options: { id: "17" } }];
  ui = {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    request: vi.fn((options: Callback) => calls.push(options)),
    uploadFile: vi.fn((options: Callback) => uploads.push(options)),
    downloadFile: vi.fn((options: Callback) => downloads.push(options)),
    login: vi.fn(({ success }: Callback) => success({ code: "test-code" })),
    reLaunch: vi.fn(), showToast: vi.fn(), setTabBarItem: vi.fn(),
  };
  vi.stubGlobal("uni", ui);
  vi.stubGlobal("getCurrentPages", () => pages);
  const pinia = createPinia();
  setActivePinia(pinia);
  installSessionHandling(pinia);
  clearSessionTarget();
});

describe("session bootstrap and account isolation", () => {
  it("restores a teacher before protected loaders choose their API and coalesces simultaneous login", async () => {
    setStoredToken("old-disk-token");
    const a = ensurePageSession("teacher");
    const b = ensurePageSession("teacher");
    await Promise.resolve();
    expect(ui.login).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);
    answer(calls[0], 200, { status: "success", token: "fresh-teacher", user: { id: 9, role: "teacher", name: "Teacher" } });
    expect(await a).toBe(true);
    expect(await b).toBe(true);
    expect(useAuthStore().isTeacher).toBe(true);
  });

  it("preserves a notification target through binding and login", async () => {
    rememberLaunchTarget("pages/task-detail/index", { id: 17, token: "must-not-be-kept" });
    const ready = ensurePageSession("student");
    await Promise.resolve();
    answer(calls[0], 200, { status: "need_bind", bind_ticket: "ticket" });
    expect(await ready).toBe(false);
    expect(ui.reLaunch).toHaveBeenCalledTimes(1);
    pages = [{ route: "pages/bind/index" }];
    const binding = useAuthStore().bindStudent("Student", "1001");
    answer(calls[1], 200, { status: "success", token: "bound", user: { id: 1, role: "student", name: "Student" } });
    await binding;
    finishSessionLogin();
    expect(ui.reLaunch).toHaveBeenLastCalledWith({ url: "/pages/task-detail/index?id=17" });
  });

  it("coalesces concurrent 401 navigation and clears both stored and reactive identity", async () => {
    loggedIn();
    const a = request({ path: "/private-a" }).catch(error => error.statusCode);
    const b = request({ path: "/private-b" }).catch(error => error.statusCode);
    answer(calls[0], 401, { detail: "expired" });
    answer(calls[1], 401, { detail: "expired" });
    expect(await a).toBe(401);
    expect(await b).toBe(409);
    expect(useAuthStore().user).toBeNull();
    expect(useAuthStore().token).toBe("");
    expect(ui.reLaunch).toHaveBeenCalledTimes(1);
  });

  it.each([200, 401])("discards an old account HTTP %s without invalidating a new login", async status => {
    loggedIn();
    const pending = request({ path: "/private" }).catch(error => error.statusCode);
    loggedIn("token-b", "teacher");
    answer(calls[0], status, { success: true, data: { private: "A" } });
    expect(await pending).toBe(409);
    expect(useAuthStore().token).toBe("token-b");
    expect(ui.reLaunch).not.toHaveBeenCalled();
  });

  it("does not resurrect a login result after the session was cleared", async () => {
    const pending = useAuthStore().silentLogin().catch(error => error.statusCode);
    await Promise.resolve();
    useAuthStore().clearSession();
    answer(calls[0], 200, { status: "success", token: "stale", user: { id: 1, role: "student", name: "Old" } });
    expect(await pending).toBe(409);
    expect(useAuthStore().user).toBeNull();
  });

  it("rejects a different role without issuing a protected request", async () => {
    loggedIn("student", "student");
    expect(await ensurePageSession("teacher")).toBe(false);
    expect(calls).toHaveLength(0);
    expect(ui.reLaunch).toHaveBeenLastCalledWith({ url: "/pages/home/index" });
  });

  it("keeps an unsuccessful unbind visible so an offline logout cannot immediately auto-login the old binding", async () => {
    loggedIn();
    const pending = useAuthStore().logout().catch(error => error.statusCode);
    calls[0].fail();
    expect(await pending).toBe(0);
    expect(useAuthStore().isLoggedIn).toBe(true);
    expect(ui.reLaunch).not.toHaveBeenCalled();
  });

  it("re-proves WeChat identity before retrying an expired-session logout once", async () => {
    loggedIn("expired");
    const pending = useAuthStore().logout();
    calls[0].success({ statusCode: 401, data: { detail: "expired" }, header: { "X-LanShare-Error-Code": "mp_logout_session_expired" } });
    for (let i = 0; i < 8; i++) await Promise.resolve();
    expect(calls[1].url).toContain("/api/mp/auth/login");
    answer(calls[1], 200, { status: "success", token: "refreshed", user: { id: 1, role: "student", name: "Student" } });
    for (let i = 0; i < 8; i++) await Promise.resolve();
    expect(calls[2].url).toContain("/api/mp/auth/logout");
    answer(calls[2], 200, { success: true, data: { revoked: true } });
    await pending;
    expect(useAuthStore().isLoggedIn).toBe(false);
    expect(ui.reLaunch).not.toHaveBeenCalled();
  });

  it("preserves stable error codes without treating a failed bind as current-session expiry", async () => {
    loggedIn();
    const pending = request({ path: "/api/mp/auth/bind/student", method: "POST", auth: false }).catch(error => error);
    calls[0].success({ statusCode: 400, data: { detail: "ticket invalid" }, header: { "x-lanshare-error-code": "mp_bind_ticket_invalid" } });
    const error = await pending;
    expect(error.code).toBe("mp_bind_ticket_invalid");
    expect(error.message).toBe("ticket invalid");
    expect(useAuthStore().isLoggedIn).toBe(true);
  });

  it("handles upload/download authentication expiry through the same session invalidation", async () => {
    loggedIn();
    const pending = uploadFile({ path: "/upload", filePath: "tmp.png" }).catch(error => error.statusCode);
    answer(uploads[0], 401, JSON.stringify({ detail: "expired" }));
    expect(await pending).toBe(401);
    expect(useAuthStore().user).toBeNull();
    loggedIn("new");
    const download = downloadProtectedTempFile("/file").catch(error => error.message);
    answer(downloads[0], 401, "");
    await download;
    expect(useAuthStore().user).toBeNull();
  });

  it("retains only registered page IDs and never credentials or external redirect targets", () => {
    expect(sanitizeSessionTarget("https://outside.invalid/?id=1")).toBeNull();
    expect(sanitizeSessionTarget("pages/bind/index")).toBeNull();
    expect(sanitizeSessionTarget("pages/teacher-grade/index?id=3&sid=7&token=secret&next=https://outside.invalid"))
      .toBe("/pages/teacher-grade/index?id=3&sid=7");
  });
});
