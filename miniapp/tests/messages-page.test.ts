import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it, vi } from "vitest";

const localRequire = createRequire(import.meta.url);
const ts = localRequire("typescript");
const vue = localRequire("vue");
const pageScript = readFileSync(new URL("../src/pages/messages/index.vue", import.meta.url), "utf8")
  .split('<script setup lang="ts">')[1].split("</script>")[0];

function item(id: number, unread = true) {
  return { id, title: `t${id}`, body_preview: "b", category: "x", is_unread: unread, created_at: "2026-01-01" };
}

function harness(pages: Array<ReturnType<typeof item>[]> = [[item(7)]]) {
  const request = vi.fn(async (options: { path: string }) => {
    if (options.path.startsWith("/api/message-center/items")) {
      const offset = Number(/offset=(\d+)/.exec(options.path)?.[1] ?? 0);
      return { items: pages.find((page, index) => pages.slice(0, index).flat().length === offset) ?? [] };
    }
    return {};
  });
  const redirectToLogin = vi.fn();
  const uni = { showToast: vi.fn(), navigateTo: vi.fn(), stopPullDownRefresh: vi.fn() };
  const code = ts.transpileModule(`${pageScript}\nexport const testPage = { items, loadItems, openItem, loadMore, hasMore, expandedId };`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const imports: Record<string, unknown> = {
    vue,
    "@dcloudio/uni-app": { onShow: vi.fn(), onPullDownRefresh: vi.fn(), onReachBottom: vi.fn() },
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

describe("message centre paging and expand (B11)", () => {
  it("pages by offset and drops cards that shifted between pages", async () => {
    const first = Array.from({ length: 50 }, (_, index) => item(100 - index));
    const { page, request } = harness([first, [item(51), item(10)]]);
    await page.loadItems();
    expect(page.hasMore.value).toBe(true);
    await page.loadMore();
    expect(request.mock.calls.at(-1)?.[0].path).toContain("offset=50");
    // id 51 was already on the first page (moved after a read); it must not duplicate.
    expect(page.items.value).toHaveLength(51);
    expect(page.items.value.at(-1).id).toBe(10);
    expect(new Set(page.items.value.map((entry: { id: number }) => entry.id)).size).toBe(page.items.value.length);
    expect(page.hasMore.value).toBe(false);
  });

  it("expands a notification without a deep link instead of doing nothing", async () => {
    const { page } = harness([[item(7, false)]]);
    await page.loadItems();
    await page.openItem(page.items.value[0]);
    expect(page.expandedId.value).toBe(7);
    await page.openItem(page.items.value[0]);
    expect(page.expandedId.value).toBeNull();
  });
});
