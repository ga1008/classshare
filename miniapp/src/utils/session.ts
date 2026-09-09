import type { Pinia } from "pinia";
import { useAuthStore } from "../stores/auth";
import { setUnauthorizedHandler } from "./api";
import { applyRoleTabs, resetRoleTabs } from "./tabs";
import { sanitizeSessionTarget, sessionTargetAllowed, type SessionRole } from "./session-target";

let pendingTarget: string | null = null;
let loginRedirecting = false;

function currentTarget(): string | null {
  const pages = getCurrentPages();
  const page = pages[pages.length - 1] as { route?: string; options?: Record<string, unknown> } | undefined;
  return page ? sanitizeSessionTarget(page.route || "", page.options || {}) : null;
}

export function rememberLaunchTarget(path: string, query: Record<string, unknown> = {}): void {
  const target = sanitizeSessionTarget(path, query);
  if (target) pendingTarget = target;
}

export function enterLoginPage(): void {
  loginRedirecting = false;
}

export function clearSessionTarget(): void {
  pendingTarget = null;
  loginRedirecting = false;
}

export function redirectToLogin(): void {
  const target = currentTarget();
  if (target) pendingTarget = target;
  const pages = getCurrentPages();
  const route = pages[pages.length - 1]?.route;
  if (loginRedirecting || route === "pages/welcome/index" || route === "pages/bind/index") return;
  loginRedirecting = true;
  uni.reLaunch({ url: "/pages/welcome/index", fail: () => { loginRedirecting = false; } });
}

export function finishSessionLogin(): void {
  const role = useAuthStore().user?.role;
  let target = pendingTarget || "/pages/home/index";
  if (!role || !sessionTargetAllowed(target, role)) {
    target = "/pages/home/index";
    uni.showToast({ title: "当前账号不能访问该页面", icon: "none" });
  }
  clearSessionTarget();
  uni.reLaunch({ url: target });
}

/** All protected page loaders await this before choosing a role-specific API. */
export async function ensurePageSession(requiredRole?: SessionRole): Promise<boolean> {
  const auth = useAuthStore();
  try {
    if (await auth.ensureSession() !== "success") {
      redirectToLogin();
      return false;
    }
    if (requiredRole && auth.user?.role !== requiredRole) {
      uni.showToast({ title: "当前账号不能访问该页面", icon: "none" });
      clearSessionTarget();
      uni.reLaunch({ url: "/pages/home/index" });
      return false;
    }
    applyRoleTabs(auth.user?.role);
    return true;
  } catch {
    redirectToLogin();
    return false;
  }
}

export function installSessionHandling(pinia: Pinia): void {
  const auth = useAuthStore(pinia);
  setUnauthorizedHandler(() => {
    auth.clearSession();
    resetRoleTabs();
    redirectToLogin();
  });
}
