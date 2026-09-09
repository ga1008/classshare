/** Display the server's classification; answer format and submission existence stay separate. */
export type AssessmentKind = "homework" | "midterm" | "final";
export interface AssessmentClassification {
  assessment_kind?: AssessmentKind | null;
  assessment_kind_label?: string;
  classification_status?: string;
  source_feature?: string;
  has_exam_paper?: boolean;
  answer_mode?: string;
}

export const ASSESSMENT_FILTER_OPTIONS = [
  { value: "", label: "全部任务" },
  { value: "homework", label: "平时作业" },
  { value: "midterm", label: "期中测验" },
  { value: "final", label: "期末测验" },
] as const;

export function assessmentLabel(value: AssessmentClassification | null | undefined): string {
  return value?.assessment_kind_label || (value?.source_feature === "personal_stage" ? "个人阶段试炼" : "历史任务");
}

export function isFormalAssessment(value: AssessmentClassification | null | undefined): boolean {
  return value?.assessment_kind === "midterm" || value?.assessment_kind === "final";
}

export function matchesAssessmentKind(value: AssessmentClassification, kind: string): boolean {
  return !kind || value.assessment_kind === kind;
}

export interface SubmissionPresence {
  has_answer_submission?: boolean;
  is_absence_score?: boolean;
  score_visible?: boolean;
  score?: number | null;
  grade_display_state?: string;
  is_returned?: boolean;
  resubmission_state?: "none" | "open" | "expired" | "invalid";
}

export function hasAnswerSubmission(value: SubmissionPresence | null | undefined): boolean {
  if (!value || value.is_absence_score) return false;
  return value.has_answer_submission !== false;
}

export function canEnterAnswerMode(value: SubmissionPresence | null | undefined, accepting: boolean, canSubmit?: boolean): boolean {
  if (typeof canSubmit === "boolean") return canSubmit;
  if (!value?.is_absence_score && value?.is_returned) return value.resubmission_state === "open";
  return accepting && !hasAnswerSubmission(value);
}

export function visibleTaskScore(value: SubmissionPresence | null | undefined): number | null {
  return value?.score_visible === false ? null : value?.score ?? null;
}

export function classroomGradeKey(value: { course_id: number; class_offering_id?: number | null; semester_id?: number | null }): string {
  return `${value.class_offering_id ?? "legacy"}:${value.semester_id ?? "legacy"}:${value.course_id}`;
}

export function assessmentNotificationTarget(link: string, metadata: Record<string, unknown> = {}, teacher = false): string | null {
  if (/^\/report-card(?:[?#]|$)/.test(link)) return teacher ? null : "/pages/report-card/index";
  const task = /^\/(?:assignment|exam\/take)\/(\d+)(?:[/?#]|$)/.exec(link);
  const submission = /^\/submission\/(\d+)(?:[/?#]|$)/.exec(link);
  const assignmentId = task?.[1] || (submission && /^\d+$/.test(String(metadata.assignment_id)) ? String(metadata.assignment_id) : "");
  if (!assignmentId) return null;
  if (teacher && submission) return `/pages/teacher-grade/index?id=${assignmentId}&sid=${submission[1]}`;
  return `/pages/${teacher ? "teacher-task" : "task-detail"}/index?id=${assignmentId}`;
}
