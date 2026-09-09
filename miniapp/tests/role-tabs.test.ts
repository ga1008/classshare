import { beforeEach, describe, expect, it, vi } from "vitest";

type TabCall = { text: string; success: () => void; fail: () => void };
let route: string;
let calls: TabCall[];
let tabs: typeof import("../src/utils/tabs");

beforeEach(async () => {
  vi.resetModules();
  route = "pages/home/index";
  calls = [];
  vi.stubGlobal("getCurrentPages", () => [{ route }]);
  vi.stubGlobal("uni", { setTabBarItem: vi.fn((options: TabCall) => calls.push(options)) });
  tabs = await import("../src/utils/tabs");
});

describe("role tabs after deep links and account changes", () => {
  it("waits for a real tab page after a teacher opens a detail notification", () => {
    route = "pages/teacher-grade/index";
    tabs.applyRoleTabs("teacher");
    expect(calls).toHaveLength(0);
    route = "pages/home/index";
    tabs.applyRoleTabs("teacher");
    expect(calls[0].text).toBe("工作台");
    calls[0].success();
    tabs.applyRoleTabs("teacher");
    expect(calls).toHaveLength(1);
  });

  it("does not cache a failed platform update and retries on the next show", () => {
    tabs.applyRoleTabs("teacher");
    calls[0].fail();
    expect(calls).toHaveLength(1);
    tabs.applyRoleTabs("teacher");
    expect(calls).toHaveLength(2);
    calls[1].success();
    tabs.applyRoleTabs("teacher");
    expect(calls).toHaveLength(2);
  });

  it("serializes a role switch so an older teacher update cannot win last", () => {
    tabs.applyRoleTabs("teacher");
    tabs.applyRoleTabs("student");
    expect(calls).toHaveLength(1);
    calls[0].success();
    expect(calls).toHaveLength(2);
    expect(calls[1].text).toBe("我的");
    calls[1].success();
    tabs.applyRoleTabs("student");
    expect(calls).toHaveLength(2);
  });

  it("does not reuse a completion from before logout, even for the same role", () => {
    tabs.applyRoleTabs("teacher");
    tabs.resetRoleTabs();
    tabs.applyRoleTabs("teacher");
    expect(calls).toHaveLength(1);
    calls[0].success();
    expect(calls).toHaveLength(2);
    calls[1].success();
    tabs.applyRoleTabs("teacher");
    expect(calls).toHaveLength(2);
  });

  it("keeps logout idle when a stale request fails after the page has left", () => {
    tabs.applyRoleTabs("teacher");
    tabs.resetRoleTabs();
    route = "pages/welcome/index";
    calls[0].fail();
    expect(calls).toHaveLength(1);
    route = "pages/me/index";
    tabs.applyRoleTabs("student");
    expect(calls[1].text).toBe("我的");
  });
});
