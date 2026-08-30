/**
 * 角色化 tabBar：学生第 4 tab = 我的，教师第 4 tab = 工作台。
 * 幂等，onShow 里随便调；记住上次应用的角色避免重复 setTabBarItem 闪动。
 */

type Role = "student" | "teacher";

let appliedRole: Role | null = null;

export function applyRoleTabs(role: Role | undefined | null): void {
  if (!role || role === appliedRole) return;
  appliedRole = role;
  const isTeacher = role === "teacher";
  uni.setTabBarItem({
    index: 3,
    text: isTeacher ? "工作台" : "我的",
    iconPath: isTeacher ? "static/tab/work.png" : "static/tab/me.png",
    selectedIconPath: isTeacher ? "static/tab/work-active.png" : "static/tab/me-active.png",
  });
}

/** 登出时复位，避免换绑后残留上一个角色的 tab 文案。 */
export function resetRoleTabs(): void {
  appliedRole = null;
}
