import { describe, expect, it, vi } from "vitest";

async function load(configResponse: Record<string, string> | Error) {
  vi.resetModules();
  const request = vi.fn(async (options: { path: string }) => {
    if (options.path.endsWith("/subscribe/config")) {
      if (configResponse instanceof Error) throw configResponse;
      return { templates: configResponse };
    }
    return {};
  });
  vi.doMock("../src/utils/api", () => ({ request }));
  const uni = { requestSubscribeMessage: vi.fn() };
  (globalThis as { uni?: unknown }).uni = uni;
  const mod = await import("../src/utils/subscribe");
  return { mod, request, uni };
}

describe("subscribe-message request (F2)", () => {
  it("fetches the template config first instead of silently skipping the prompt", async () => {
    const { mod, uni } = await load({ deadline: "T1", graded: "T2" });
    const outcome = await mod.requestSubscribe(["graded", "deadline", "nudge"]);
    expect(outcome).toBe("asked");
    expect(uni.requestSubscribeMessage).toHaveBeenCalledTimes(1);
    expect(uni.requestSubscribeMessage.mock.calls[0][0].tmplIds).toEqual(["T2", "T1"]);
  });

  it("asks synchronously once the config is cached and reports accepted keys", async () => {
    const { mod, uni, request } = await load({ deadline: "T1" });
    await mod.prefetchSubscribeConfig();
    uni.requestSubscribeMessage.mockImplementation((options: { success: (res: unknown) => void }) =>
      options.success({ T1: "accept", errMsg: "ok" }));
    const pending = mod.requestSubscribe(["deadline"]);
    expect(uni.requestSubscribeMessage).toHaveBeenCalledTimes(1);
    expect(await pending).toBe("asked");
    await Promise.resolve();
    const report = request.mock.calls.find(([options]) => options.path.endsWith("/subscribe/report"));
    expect(report?.[0].data).toEqual({ accepted: ["deadline"] });
  });

  it("returns no_config when the config cannot be fetched and never throws", async () => {
    const { mod, uni } = await load(new Error("offline"));
    expect(await mod.requestSubscribe(["graded"])).toBe("no_config");
    expect(uni.requestSubscribeMessage).not.toHaveBeenCalled();
  });
});
