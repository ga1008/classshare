/**
 * 角色化 tabBar：学生第 4 tab = 我的，教师第 4 tab = 工作台。
 * 幂等，onShow 里随便调；记住上次应用的角色避免重复 setTabBarItem 闪动。
 */

type Role = "student" | "teacher";

const TAB_ROUTES = new Set([
  "pages/home/index", "pages/tasks/index", "pages/classroom/index", "pages/me/index",
]);
let appliedRole: Role | null = null;
let desiredRole: Role | null = null;
let generation = 0;
let pending: { role: Role; generation: number } | null = null;

function isTabPage(): boolean {
  const pages = getCurrentPages();
  const route = pages[pages.length - 1]?.route || "";
  return TAB_ROUTES.has(route.replace(/^\//, ""));
}

export function applyRoleTabs(role: Role | undefined | null): void {
  if (!role) return;
  desiredRole = role;
  if (!isTabPage() || pending || role === appliedRole) return;
  const operation = { role, generation };
  pending = operation;
  const finish = (success: boolean): void => {
    if (pending !== operation) return;
    pending = null;
    appliedRole = success && operation.generation === generation ? operation.role : null;
    // Serialize changes: an older callback cannot win over a newer account.
    // A failed request for the same role retries on the next page show, avoiding
    // an immediate retry loop when the platform cannot update its tab bar.
    if (desiredRole && (desiredRole !== operation.role || operation.generation !== generation)) {
      applyRoleTabs(desiredRole);
    }
  };
  const isTeacher = role === "teacher";
  try {
    uni.setTabBarItem({
      index: 3,
      text: isTeacher ? "工作台" : "我的",
      iconPath: isTeacher ? "static/tab/work.png" : "static/tab/me.png",
      selectedIconPath: isTeacher ? "static/tab/work-active.png" : "static/tab/me-active.png",
      success: () => finish(true),
      fail: () => finish(false),
    });
  } catch {
    finish(false);
  }
}

/** 登出时复位，避免换绑后残留上一个角色的 tab 文案。 */
export function resetRoleTabs(): void {
  generation += 1;
  appliedRole = null;
  desiredRole = null;
  // Keep a pending platform call serialized until its callback arrives. A new
  // login records its desired role and is applied once that old call finishes.
}
