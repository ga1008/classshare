/** Small, platform-independent contracts for task drafts and in-flight writes. */
export function taskDraftKey(apiBase: string, user: { id: number; role: string } | null,
  assignmentId: string, revision: string): string {
  if (!user || !assignmentId || !revision) return "";
  return ["lanshareTaskDraft:v2", apiBase.replace(/\/$/, ""), user.role, user.id, assignmentId, revision]
    .map(String).map(encodeURIComponent).join(":");
}

export function remainingAt(remaining: number | null, receivedAt: number, now: number): number | null {
  return remaining === null ? null : Math.max(0, remaining - Math.floor(Math.max(0, now - receivedAt) / 1000));
}

/** One mutation at a time; callers wait for a draft before uploading/submitting. */
export class TaskWriteQueue {
  private active: Promise<unknown> | null = null;
  get busy(): boolean { return this.active !== null; }
  async idle(): Promise<void> { await this.active?.catch(() => undefined); }
  async run<T>(operation: () => Promise<T>): Promise<T> {
    if (this.active) throw new Error("草稿正在同步，请稍候");
    // Register ownership before operation can yield or a second action starts.
    const pending = Promise.resolve().then(operation);
    this.active = pending;
    try { return await pending; } finally { if (this.active === pending) this.active = null; }
  }
}
