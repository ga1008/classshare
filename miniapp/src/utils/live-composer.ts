/**
 * 教师随堂测表单 → 提交载荷的纯函数（C10 修复）。
 *
 * 表单是固定 4 槽；教师可能留空中间槽再标正确答案。这里先给每个槽一个
 * 稳定 id，过滤空槽后按 id 而不是下标标记 is_correct，所以"留空 B、标 C"
 * 不会把正确答案错位到 D；被标为正确的槽为空时直接拒绝。
 */

export interface QuizOptionPayload {
  label: string;
  is_correct: boolean;
}

export type QuizBuildResult =
  | { ok: true; options: QuizOptionPayload[] }
  | { ok: false; error: string };

export const QUIZ_MIN_OPTIONS = 2;

export function buildQuizOptions(slots: readonly string[], correctSlot: number): QuizBuildResult {
  const filled = slots
    .map((label, slot) => ({ slot, label: (label ?? "").trim() }))
    .filter((entry) => entry.label.length > 0);
  if (filled.length < QUIZ_MIN_OPTIONS) {
    return { ok: false, error: `至少填写 ${QUIZ_MIN_OPTIONS} 个选项` };
  }
  const correct = filled.find((entry) => entry.slot === correctSlot);
  if (!correct) {
    return { ok: false, error: "请把正确答案标在已填写的选项上" };
  }
  return {
    ok: true,
    options: filled.map((entry) => ({ label: entry.label, is_correct: entry.slot === correctSlot })),
  };
}
