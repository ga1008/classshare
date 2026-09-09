import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it, vi } from "vitest";

const require = createRequire(import.meta.url);
const ts = require("../node_modules/typescript");
const vue = require("../node_modules/vue");
const source = readFileSync(new URL("../src/pages/bind/index.vue", import.meta.url), "utf8");
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1];
const compiled = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function harness() {
  const auth = { bindTicket: "ticket", silentLogin: vi.fn(async () => "need_bind"),
    bindStudent: vi.fn(async () => {}), bindTeacher: vi.fn(async () => {}) };
  const uni = { showToast: vi.fn(), reLaunch: vi.fn() };
  const modules = { "vue": vue, "@dcloudio/uni-app": { onLoad: vi.fn() }, "../../stores/auth": { useAuthStore: () => auth } };
  const page = new Function("require", "exports", "uni", `${compiled}\nreturn {submit, submitting, studentName, studentIdNumber, role};`)(id => modules[id], {}, uni);
  page.studentName.value = "Student";
  page.studentIdNumber.value = "1001";
  return { auth, uni, page };
}

describe("binding page recovery", () => {
  it("refreshes an expired or consumed ticket using the stable code", async () => {
    const h = harness();
    h.auth.bindStudent.mockRejectedValueOnce({ code: "mp_bind_ticket_invalid", statusCode: 400 });
    await h.page.submit();
    expect(h.auth.bindTicket).toBe("");
    expect(h.auth.silentLogin).toHaveBeenCalledTimes(1);
    expect(h.page.submitting.value).toBe(false);
  });
  it("does not refresh tickets for an ordinary invalid-account response", async () => {
    const h = harness();
    h.auth.bindStudent.mockRejectedValueOnce({ statusCode: 400 });
    await h.page.submit();
    expect(h.auth.silentLogin).not.toHaveBeenCalled();
  });
  it("prevents a second submit while the first is obtaining a ticket", async () => {
    const h = harness();
    h.auth.bindTicket = "";
    let resolve!: (value: string) => void;
    h.auth.silentLogin.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const first = h.page.submit();
    await h.page.submit();
    expect(h.auth.silentLogin).toHaveBeenCalledTimes(1);
    h.auth.bindTicket = "new-ticket";
    resolve("need_bind");
    await first;
    expect(h.auth.bindStudent).toHaveBeenCalledTimes(1);
  });
  it("recovers a binding that committed before its response was lost", async () => {
    const h = harness();
    h.auth.bindStudent.mockRejectedValueOnce({ code: "mp_bind_ticket_invalid", statusCode: 400 });
    h.auth.silentLogin.mockResolvedValueOnce("success");
    await h.page.submit();
    expect(h.uni.reLaunch).toHaveBeenCalledWith({ url: "/pages/welcome/index" });
  });
});
