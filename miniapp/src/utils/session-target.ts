/** Only registered application pages and their public navigation IDs may survive login. */
export type SessionRole = "student" | "teacher";

const PAGE_ROLES: Record<string, SessionRole | null> = {
  "/pages/home/index": null,
  "/pages/tasks/index": null,
  "/pages/classroom/index": null,
  "/pages/me/index": null,
  "/pages/live/index": null,
  "/pages/messages/index": null,
  "/pages/task-detail/index": "student",
  "/pages/report-card/index": "student",
  "/pages/wrong-book/index": "student",
  "/pages/growth/index": "student",
  "/pages/teacher-task/index": "teacher",
  "/pages/teacher-grade/index": "teacher",
};

export function sanitizeSessionTarget(path: string, query: Record<string, unknown> = {}): string | null {
  const [rawPath, search = ""] = String(path || "").split("?");
  const route = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
  if (!Object.prototype.hasOwnProperty.call(PAGE_ROLES, route)) return null;
  const values: Record<string, unknown> = { ...query };
  for (const pair of search.split("&")) {
    const [key, value = ""] = pair.split("=");
    try { if (key) values[decodeURIComponent(key)] = decodeURIComponent(value); } catch { /* ignore invalid query */ }
  }
  const allowed = route === "/pages/live/index" ? ["oid"] :
    route === "/pages/teacher-grade/index" ? ["id", "sid"] :
      ["/pages/task-detail/index", "/pages/teacher-task/index"].includes(route) ? ["id"] : [];
  const parts = allowed.filter(key => /^[1-9]\d*$/.test(String(values[key] ?? "")))
    .map(key => `${key}=${values[key]}`);
  return `${route}${parts.length ? `?${parts.join("&")}` : ""}`;
}

export function sessionTargetAllowed(target: string, role: SessionRole): boolean {
  const route = target.split("?")[0];
  return Object.prototype.hasOwnProperty.call(PAGE_ROLES, route) &&
    (!PAGE_ROLES[route] || PAGE_ROLES[route] === role);
}
