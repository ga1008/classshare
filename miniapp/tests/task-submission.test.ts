import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as assessment from "../src/utils/assessment";
import * as flow from "../src/utils/task-submission";

const localRequire = createRequire(import.meta.url);
const ts = localRequire("typescript");
const vue = localRequire("vue");
const pageScript = readFileSync(new URL("../src/pages/task-detail/index.vue", import.meta.url), "utf8")
  .split('<script setup lang="ts">')[1].split("</script>")[0];

function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function detail(overrides = {}) {
  return {
    assignment: { id: "42", is_accepting_submissions: true, can_submit: true, draft_revision: "initial",
      submission_version: "unsubmitted", remaining_seconds: null, answer_remaining_seconds: null, server_now_ms: Date.now() },
    submission: null, paper: null, group: null, ...overrides,
  };
}

function harness() {
  const store = new Map<string, unknown>();
  const auth = vue.reactive({ user: { id: 1, role: "student" } });
  const hooks: Record<string, Function> = {};
  const request = vi.fn(async (options: { path: string }) => options.path.endsWith("/draft") ? {} : detail());
  const uploadFile = vi.fn(async () => ({ files_by_question: {} }));
  const uni = { getStorageSync: (key: string) => store.get(key), setStorageSync: (key: string, value: unknown) => store.set(key, value),
    removeStorageSync: (key: string) => store.delete(key), showToast: vi.fn(),
    showModal: vi.fn((options: { success?: Function }) => options.success?.({ confirm: true })) };
  const exposed = "loadDetail, saveLocalDraft, saveServerDraft, uploadEntries, submit, reconcileSubmission, assignmentId, detail, answers, plainAnswer, questionFiles, pendingSubmissionVersion, localDraftKey, remainingSeconds, startCountdown, clearFiles, verifyDraftFiles, onTextInput";
  const code = ts.transpileModule(`${pageScript}\nexport const testPage = { ${exposed} };`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const imports: Record<string, unknown> = {
    vue, "@dcloudio/uni-app": Object.fromEntries(["onLoad", "onShow", "onHide", "onUnload"].map((key) => [key, (callback: Function) => { hooks[key] = callback; }])),
    "../../config": { API_BASE: "https://api.test" }, "../../stores/auth": { useAuthStore: () => auth },
    "../../utils/session": { ensurePageSession: async () => true, redirectToLogin: vi.fn() },
    "../../utils/api": { request, uploadFile }, "../../utils/preview": { previewProtectedFile: vi.fn() },
    "../../utils/subscribe": { requestSubscribe: vi.fn() }, "../../utils/assessment": assessment, "../../utils/task-submission": flow,
  };
  const module = { exports: {} as { testPage: any } };
  new Function("require", "exports", "module", "uni", code)((name: string) => imports[name], module.exports, module, uni);
  module.exports.testPage.assignmentId.value = "42";
  return { page: module.exports.testPage, request, uploadFile, store, auth, uni, hooks };
}

afterEach(() => { vi.useRealTimers(); });

describe("task writes and account-bound drafts", () => {
  it("separates API, role, user, task, and returned round and never uses a legacy key", () => {
    const key = (api = "a", role = "student", id = 1, task = "42", revision = "initial") => flow.taskDraftKey(api, { role, id }, task, revision);
    expect(new Set([key(), key("b"), key("a", "teacher"), key("a", "student", 2), key("a", "student", 1, "43"), key("a", "student", 1, "42", "return:2")]).size).toBe(6);
    expect(flow.taskDraftKey("a", null, "42", "initial")).toBe("");
    expect(key()).not.toBe("lanshareTaskDraft:42");
  });

  it("does not recover another student's answers on the same device", async () => {
    const { page, store, auth } = harness();
    store.set("lanshareTaskDraft:42", { plain: "legacy secret" });
    await page.loadDetail();
    expect(page.plainAnswer.value).toBe("");
    page.plainAnswer.value = "A secret";
    page.saveLocalDraft();
    const aKey = page.localDraftKey.value;
    auth.user = { id: 2, role: "student" };
    // onHide after account invalidation must not write old answers to B's key.
    page.saveLocalDraft();
    await page.loadDetail();
    expect(page.plainAnswer.value).toBe("");
    expect(store.get(aKey)).toMatchObject({ plain: "A secret" });
    expect(store.has(page.localDraftKey.value)).toBe(false);
  });

  it("waits for an in-flight draft before upload and blocks an early submit", async () => {
    const { page, request, uploadFile } = harness();
    await page.loadDetail();
    page.plainAnswer.value = "my answer";
    const saving = deferred();
    request.mockImplementation((options: any) => options.method === "POST" ? saving.promise : Promise.resolve({}));
    const draft = page.saveServerDraft();
    const uploading = page.uploadEntries({ id: "attachment" }, [{ name: "a.png", path: "/a.png", size: 1, kind: "image" }]);
    await page.submit();
    expect(uploadFile).not.toHaveBeenCalled();
    expect(request.mock.calls.some(([options]) => options.path.endsWith("/submit"))).toBe(false);
    saving.resolve({});
    await draft;
    await uploading;
    expect(uploadFile).toHaveBeenCalledTimes(1);
  });

  it("binds text, upload, and attachment removal to the round the student opened", async () => {
    const { page, request, uploadFile } = harness();
    await page.loadDetail();
    page.plainAnswer.value = "previous round content";
    await page.saveServerDraft();
    expect(request.mock.calls.find(([options]: any) => options.method === "POST")?.[0])
      .toMatchObject({ data: { expected_submission_version: "unsubmitted" } });
    await page.uploadEntries({ id: "attachment" }, [{ name: "a.png", path: "/a.png", size: 1, kind: "image" }]);
    expect(uploadFile.mock.calls[0]?.[0])
      .toMatchObject({ formData: { expected_submission_version: "unsubmitted" } });
    page.questionFiles.attachment = [{ id: 1 }];
    await page.clearFiles({ id: "attachment" });
    expect(request.mock.calls.find(([options]: any) => options.data?.replace_question_ids)?.[0])
      .toMatchObject({ data: { expected_submission_version: "unsubmitted" } });
  });

  it("does not adopt a refreshed round in the middle of a multi-file upload", async () => {
    const { page, uploadFile } = harness();
    await page.loadDetail();
    uploadFile.mockImplementationOnce(async () => {
      // A foreground/detail refresh observes a teacher's new return while the
      // first file is in flight. The remaining old action must still conflict.
      page.detail.value.assignment.submission_version = "new-returned-round";
      return { files_by_question: {} };
    });
    await page.uploadEntries({ id: "attachment" }, ["a.png", "b.png"].map((name) => ({
      name, path: `/${name}`, size: 1, kind: "image",
    })));
    expect(uploadFile.mock.calls).toHaveLength(2);
    expect(uploadFile.mock.calls.map(([options]: any) => options.formData.expected_submission_version))
      .toEqual(["unsubmitted", "unsubmitted"]);
  });

  it("keeps a timeout pending while offline and checks before another POST", async () => {
    const { page, request, store } = harness();
    await page.loadDetail();
    page.plainAnswer.value = "answer";
    request.mockRejectedValue(new Error("offline"));
    await page.submit();
    expect(page.pendingSubmissionVersion.value).toBe("unsubmitted");
    expect(store.get(page.localDraftKey.value)).toMatchObject({ pending_submission_version: "unsubmitted" });
    const postCount = () => request.mock.calls.filter(([options]) => options.path.endsWith("/submit")).length;
    expect(postCount()).toBe(1);
    await page.submit();
    expect(postCount()).toBe(1);
    request.mockResolvedValue(detail({ submission: { has_answer_submission: true, is_returned: false, answers: [] } }));
    await page.submit();
    expect(page.pendingSubmissionVersion.value).toBeNull();
    expect(postCount()).toBe(1);
    expect(store.has(page.localDraftKey.value)).toBe(false);
  });

  it("uses the personal reopen permission even when the global assignment is closed", () => {
    expect(assessment.canEnterAnswerMode({ is_returned: true, resubmission_state: "open" }, false, true)).toBe(true);
    expect(assessment.canEnterAnswerMode({ is_returned: true, resubmission_state: "expired" }, true, false)).toBe(false);
    expect(assessment.canEnterAnswerMode({ is_returned: true, resubmission_state: "invalid" }, true)).toBe(false);
  });

  it("preserves current typing during onShow clock/status refresh", async () => {
    const { page, request } = harness();
    await page.loadDetail();
    page.plainAnswer.value = "new local edits";
    request.mockResolvedValue(detail());
    await page.loadDetail();
    expect(page.plainAnswer.value).toBe("new local edits");
  });

  it("blocks duplicate submit after success even when loading the result fails", async () => {
    const { page, request } = harness();
    await page.loadDetail();
    page.plainAnswer.value = "answer";
    request.mockImplementation((options: any) => options.path.endsWith("/submit") ? Promise.resolve({}) : Promise.reject(new Error("offline")));
    await page.submit();
    await page.submit();
    expect(request.mock.calls.filter(([options]) => options.path.endsWith("/submit"))).toHaveLength(1);
    const post = request.mock.calls.find(([options]) => options.path.endsWith("/submit"));
    expect(post?.[0]).toMatchObject({ data: { expected_submission_version: "unsubmitted" } });
  });

  it("keeps attachment paths inside the paper question policy scope", async () => {
    const { page, request, uploadFile } = harness();
    request.mockImplementation((options: any) => Promise.resolve(options.path.endsWith("/draft") ? {} : detail({ paper: { pages: [] } })));
    await page.loadDetail();
    await page.uploadEntries({ id: "q1" }, [{ name: "answer.zip", path: "/answer.zip", size: 12, kind: "file" }]);
    expect(uploadFile.mock.calls[0]?.[0]).toMatchObject({ formData: { manifest: expect.stringContaining("exam_question_files/q1/answer.zip") } });
  });

  it("recovers server text after the first draft GET failed before allowing submission", async () => {
    const { page, request } = harness();
    request.mockImplementation((options: any) => options.path.endsWith("/draft") ? Promise.reject(new Error("offline")) : Promise.resolve(detail()));
    await page.loadDetail();
    request.mockImplementation((options: any) => Promise.resolve(options.path.endsWith("/draft")
      ? { exists: true, answers_json: JSON.stringify({ answers: [{ question: "作答", answer: "complete server answer" }] }), files_by_question: { attachment: [{ id: 1 }] } }
      : options.path.endsWith("/submit") ? {} : detail()));
    await page.submit();
    const post = request.mock.calls.find(([options]) => options.path.endsWith("/submit"));
    expect(post?.[0]).toMatchObject({ data: { answers_json: expect.stringContaining("complete server answer") } });
  });

  it("restores untouched questions after network recovery while preserving new typing", async () => {
    const { page, request } = harness();
    request.mockImplementation((options: any) => options.path.endsWith("/draft") ? Promise.reject(new Error("offline")) : Promise.resolve(detail()));
    await page.loadDetail();
    page.onTextInput("q1", { detail: { value: "new typing" } });
    request.mockResolvedValue({ exists: true, answers_json: JSON.stringify({ answers: [{ question_id: "q1", answer: "old q1" }, { question_id: "q2", answer: "server q2" }] }) });
    await page.verifyDraftFiles();
    expect(page.answers.q1).toBe("new typing");
    expect(page.answers.q2).toBe("server q2");
  });

  it("recalculates elapsed countdown time after background suspension", () => {
    expect(flow.remainingAt(120, 1000, 62000)).toBe(59);
    expect(flow.remainingAt(120, 1000, 122000)).toBe(0);
    expect(flow.remainingAt(null, 1000, 122000)).toBeNull();
  });
});
