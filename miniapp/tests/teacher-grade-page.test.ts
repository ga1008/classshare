import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it, vi } from "vitest";

const require = createRequire(import.meta.url);
const ts = require("../node_modules/typescript");
const vue = require("../node_modules/vue");
const source = readFileSync(new URL("../src/pages/teacher-grade/index.vue", import.meta.url), "utf8");
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1];
const compiled = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function deferred<T = any>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: any) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function answer(id: number, score: number | null = null, original: number | null = null) {
  return {
    assignment: { id: 1 }, student: { name: `Student ${id}` }, questions: [], paper_files: [], total_points: 100,
    submission: { id, score, score_before_late_penalty: original, late_penalty_points: original === null ? 0 : 10,
      review_revision: `revision-${id}`, resubmission_allowed: false, feedback_md: `feedback-${id}`,
      editable_feedback_md: `feedback-${id}`, feedback_blocks: [] },
  };
}

function pageHarness() {
  const requests: Array<{ args: any; response: ReturnType<typeof deferred> }> = [];
  const request = vi.fn((args: any) => {
    if (args.path.endsWith("/grading")) return Promise.resolve({ entries: [1, 2, 3].map((id) => ({
      submission_id: id, status: "submitted", student_name: `Student ${id}`,
    })) });
    const response = deferred();
    requests.push({ args, response });
    return response.promise;
  });
  const uni = { showToast: vi.fn(), showModal: vi.fn((options) => options.success?.({ confirm: true })) };
  let unload = () => {};
  const modules: Record<string, any> = {
    "vue": vue,
    "@dcloudio/uni-app": { onLoad: vi.fn(), onUnload: (fn: () => void) => { unload = fn; } },
    "../../utils/api": { request },
    "../../utils/session": { ensurePageSession: async () => true, redirectToLogin: vi.fn() },
    "../../utils/assessment": { assessmentLabel: () => "作业" },
    "../../utils/preview": {},
  };
  const scope = vue.effectScope();
  // Execute the actual page script with real Vue refs/watchers. Only platform
  // APIs and network scheduling are replaced, so navigation/save state is tested.
  const page = scope.run(() => new Function("require", "exports", "uni", `${compiled}\nreturn {
    assignmentId, index, current, review, reviewLoading, reviewFailed, reviewConflict,
    gradeScore, gradeFeedback, canGrade, saving, isDirty, loadAll, loadReview,
    saveGrade, goPrev, goNext, applyQuickScore, reloadReview
  };`)((id: string) => modules[id], {}, uni));
  page.assignmentId.value = "1";
  return { page, requests, uni, dispose: () => { unload(); scope.stop(); } };
}

async function tick() { for (let i = 0; i < 8; i++) await Promise.resolve(); }

describe("teacher grade page, actual script with delayed requests", () => {
  it("clears A immediately and discards late A/B responses while C is selected", async () => {
    const h = pageHarness();
    const loading = h.page.loadAll();
    await tick();
    await h.page.goNext();
    expect(h.page.current.value.submission_id).toBe(2);
    expect(h.page.review.value).toBeNull();
    expect(h.page.canGrade.value).toBe(false);
    await h.page.goNext();
    h.requests[1].response.resolve(answer(2, 60));
    await tick();
    expect(h.page.review.value).toBeNull();
    expect(h.page.reviewLoading.value).toBe(true);
    h.requests[2].response.resolve(answer(3, 90));
    await tick();
    h.requests[0].response.resolve(answer(1, 50));
    await loading;
    expect(h.page.review.value.submission.id).toBe(3);
    expect(h.page.gradeScore.value).toBe("90");
    expect(h.page.gradeFeedback.value).toBe("feedback-3");
    h.dispose();
  });

  it("does not submit a blank score and sends the original score with the reviewed revision", async () => {
    const h = pageHarness();
    const loading = h.page.loadAll(); await tick();
    h.requests[0].response.resolve(answer(1, 70, 80)); await loading;
    expect(h.page.gradeScore.value).toBe("80");
    expect(h.page.isDirty.value).toBe(false);
    h.page.gradeScore.value = "  ";
    await h.page.saveGrade(false);
    expect(h.requests).toHaveLength(1);
    h.page.gradeScore.value = "80";
    h.page.gradeFeedback.value = "只改评语";
    const save = h.page.saveGrade(false);
    expect(h.requests[1].args).toMatchObject({ path: "/api/submissions/1/grade", data: {
      score: 80, feedback_md: "只改评语", expected_review_revision: "revision-1",
    } });
    await h.page.goNext();
    await h.page.saveGrade(true);
    h.page.applyQuickScore(100);
    expect(h.page.current.value.submission_id).toBe(1);
    expect(h.page.gradeScore.value).toBe("80");
    expect(h.requests).toHaveLength(2);
    h.requests[1].response.resolve({ status: "success" }); await tick();
    h.requests[2].response.resolve(answer(1, 70, 80)); await save;
    expect(h.page.gradeScore.value).toBe("80");
    h.dispose();
  });

  it("preserves the local draft on a conflict and blocks retry until an explicit reload", async () => {
    const h = pageHarness();
    const loading = h.page.loadAll(); await tick();
    h.requests[0].response.resolve(answer(1, 70)); await loading;
    h.page.gradeScore.value = "85";
    h.page.gradeFeedback.value = "本地评语";
    const save = h.page.saveGrade(false);
    h.requests[1].response.reject(Object.assign(new Error("已更新"), { statusCode: 409 }));
    await save;
    expect(h.page.reviewConflict.value).toBe(true);
    expect(h.page.gradeScore.value).toBe("85");
    expect(h.page.gradeFeedback.value).toBe("本地评语");
    await h.page.saveGrade(false);
    expect(h.requests).toHaveLength(2);
    const reload = h.page.reloadReview(); await tick();
    h.requests[2].response.resolve(answer(1, 95)); await reload;
    expect(h.uni.showModal).toHaveBeenCalledWith(expect.objectContaining({ title: "未保存的修改" }));
    expect(h.page.gradeScore.value).toBe("95");
    expect(h.page.canGrade.value).toBe(true);
    h.dispose();
  });

  it("leaves a failed B load empty and ungradable, and ignores responses after unload", async () => {
    const h = pageHarness();
    const loading = h.page.loadAll(); await tick();
    h.requests[0].response.resolve(answer(1, 80)); await loading;
    await h.page.goNext();
    expect(h.page.gradeScore.value).toBe("");
    await h.page.saveGrade(false);
    h.requests[1].response.reject(new Error("offline")); await tick();
    expect(h.page.reviewFailed.value).toBe(true);
    expect(h.page.canGrade.value).toBe(false);
    const retry = h.page.loadReview();
    h.dispose();
    h.requests[2].response.resolve(answer(2, 99)); await retry;
    expect(h.page.review.value).toBeNull();
  });
});
