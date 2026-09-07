import { describe, expect, it } from "vitest";
import {
  ASSESSMENT_FILTER_OPTIONS, assessmentLabel, canEnterAnswerMode, classroomGradeKey,
  hasAnswerSubmission, isFormalAssessment, matchesAssessmentKind, visibleTaskScore,
  assessmentNotificationTarget,
} from "../src/utils/assessment";

describe("miniapp assessment and submission contracts", () => {
  it("keeps exactly three formal choices and does not infer purpose from paper format", () => {
    expect(ASSESSMENT_FILTER_OPTIONS.slice(1).map((item) => item.value)).toEqual(["homework", "midterm", "final"]);
    const paperHomework = { assessment_kind: "homework" as const, assessment_kind_label: "平时作业", has_exam_paper: true };
    const attachmentFinal = { assessment_kind: "final" as const, assessment_kind_label: "期末测验", has_exam_paper: false };
    expect(assessmentLabel(paperHomework)).toBe("平时作业");
    expect(isFormalAssessment(paperHomework)).toBe(false);
    expect(isFormalAssessment(attachmentFinal)).toBe(true);
    expect(matchesAssessmentKind(paperHomework, "final")).toBe(false);
    expect(matchesAssessmentKind(attachmentFinal, "final")).toBe(true);
  });

  it("keeps historical tasks and personal trials neutral and outside formal filters", () => {
    expect(assessmentLabel({ has_exam_paper: true })).toBe("历史任务");
    expect(assessmentLabel({ source_feature: "personal_stage" })).toBe("个人阶段试炼");
    expect(matchesAssessmentKind({ assessment_kind: null }, "midterm")).toBe(false);
    expect(matchesAssessmentKind({ assessment_kind: null }, "")).toBe(true);
  });

  it("shows a recorded zero without pretending an answer has been submitted", () => {
    const absence = { is_absence_score: true, has_answer_submission: false, score: 0, score_visible: true };
    expect(visibleTaskScore(absence)).toBe(0);
    expect(hasAnswerSubmission(absence)).toBe(false);
    expect(canEnterAnswerMode(absence, true)).toBe(true);
    expect(canEnterAnswerMode(absence, false)).toBe(false);
    expect(canEnterAnswerMode({ score: 80 }, true)).toBe(false); // Existing server contract.
    expect(canEnterAnswerMode(null, true)).toBe(true);
    expect(canEnterAnswerMode({ has_answer_submission: false }, true)).toBe(true);
  });

  it("does not expose hidden group scores or convert missing scores into zero", () => {
    expect(visibleTaskScore({ score: 98, score_visible: false })).toBeNull();
    expect(visibleTaskScore({ score: null })).toBeNull();
    expect(visibleTaskScore(null)).toBeNull();
    expect(visibleTaskScore({ score: 0 })).toBe(0);
  });

  it("separates reused courses across classrooms and semesters", () => {
    const first = { course_id: 1, class_offering_id: 10, semester_id: 20 };
    expect(classroomGradeKey(first)).not.toBe(classroomGradeKey({ ...first, semester_id: 21 }));
    expect(classroomGradeKey(first)).not.toBe(classroomGradeKey({ ...first, class_offering_id: 11 }));
    expect(classroomGradeKey({ course_id: 1 })).not.toBe(classroomGradeKey({ course_id: 2 }));
  });

  it("opens grade notifications in the correct student's or teacher's view", () => {
    expect(assessmentNotificationTarget("/assignment/8")).toBe("/pages/task-detail/index?id=8");
    expect(assessmentNotificationTarget("/assignment/8", {}, true)).toBe("/pages/teacher-task/index?id=8");
    expect(assessmentNotificationTarget("/submission/9", { assignment_id: 8 })).toBe("/pages/task-detail/index?id=8");
    expect(assessmentNotificationTarget("/submission/9", { assignment_id: 8 }, true)).toBe("/pages/teacher-grade/index?id=8&sid=9");
    expect(assessmentNotificationTarget("/report-card")).toBe("/pages/report-card/index");
    expect(assessmentNotificationTarget("/submission/9")).toBeNull();
    expect(assessmentNotificationTarget("https://unrelated.test/assignment/8")).toBeNull();
  });
});
