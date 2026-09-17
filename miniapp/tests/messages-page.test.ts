import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it, vi } from "vitest";

const localRequire = createRequire(import.meta.url);
const ts = localRequire("typescript");
const vue = localRequire("vue");
const pageScript = readFileSync(new URL("../src/pages/messages/index.vue", import.meta.url), "utf8")
  .split('<script setup lang="ts">')[1].split("</script>")[0];

function harness() {
  const request = vi.fn(async (options: { path: string }) => {
    if (options.path.startsWith("/api/message-center/items")) {
      return { items: [{ id: 7, title: "t", body_preview: "b", category: "x", is_unread: true, created_at: "2026-01-01" }] };
    }
    return {};
  });
  const redirectToLogin = vi.fn();
  const uni = { showToast: vi.fn(), navigateTo: vi.fn(), stopPullDownRefresh: vi.fn() };
  const code = ts.transpileModule(`${pageScript}\nexport const testPage = { items, loadItems, openItem };`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const imports: Record<string, unknown> = {
    vue,
    "@dcloudio/uni-app": { onShow: vi.fn(), onPullDownRefresh: vi.fn() },
    "../../utils/api": { request },
    "../../utils/session": { ensurePageSession: async () => true, redirectToLogin },
    "../../utils/format": { relativeTimeLabel: () => "" },
    "../../stores/auth": { useAuthStore: () => ({ isTeacher: false }) },
    "../../utils/assessment": { assessmentNotificationTarget: () => null },
  };
  const module = { exports: {} as { testPage: any } };
  new Function("require", "exports", "module", "uni", code)((name: string) => imports[name], module.exports, module, uni);
  return { page: module.exports.testPage, request, uni, redirectToLogin };
}

describe("message centre read state (F1)", () => {
  it("keeps the item unread when the server rejects the read mark", async () => {
    const { page, request, uni } = harness();
    await page.loadItems();
    request.mockRejectedValueOnce(new Error("offline"));
    await page.openItem(page.items.value[0]);
    expect(page.items.value[0].is_unread).toBe(true);
    expect(uni.showToast).toHaveBeenCalledTimes(1);
  });

  it("marks the item read only after the server accepted it", async () => {
    const { page, request } = harness();
    await page.loadItems();
    await page.openItem(page.items.value[0]);
    expect(page.items.value[0].is_unread).toBe(false);
    expect(request.mock.calls.at(-1)?.[0]).toMatchObject({ path: "/api/message-center/read", data: { notification_ids: [7] } });
  });

  it("redirects to login on 401 instead of faking a saved state", async () => {
    const { page, request, redirectToLogin } = harness();
    await page.loadItems();
    request.mockRejectedValueOnce(Object.assign(new Error("expired"), { statusCode: 401 }));
    await page.openItem(page.items.value[0]);
    expect(page.items.value[0].is_unread).toBe(true);
    expect(redirectToLogin).toHaveBeenCalledTimes(1);
  });
});
