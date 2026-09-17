import { describe, expect, it } from "vitest";
import { buildQuizOptions } from "../src/utils/live-composer";

describe("quiz composer option mapping (C10)", () => {
  it("keeps the correct answer on the labelled slot when a middle slot is blank", () => {
    const result = buildQuizOptions(["A", "", "C", "D"], 2);
    expect(result).toEqual({ ok: true, options: [
      { label: "A", is_correct: false }, { label: "C", is_correct: true }, { label: "D", is_correct: false },
    ] });
  });

  it("rejects a blank slot marked as the correct answer", () => {
    expect(buildQuizOptions(["A", "", "C", ""], 1)).toMatchObject({ ok: false });
    expect(buildQuizOptions(["A", "B", "", ""], 3)).toMatchObject({ ok: false });
  });

  it("requires at least two filled options and trims labels", () => {
    expect(buildQuizOptions(["  only ", "", "", ""], 0)).toMatchObject({ ok: false });
    expect(buildQuizOptions([" a ", " b ", "", ""], 1)).toEqual({ ok: true, options: [
      { label: "a", is_correct: false }, { label: "b", is_correct: true },
    ] });
  });

  it("marks exactly one option correct", () => {
    const result = buildQuizOptions(["A", "B", "C", "D"], 3);
    expect(result.ok && result.options.filter((o) => o.is_correct)).toEqual([{ label: "D", is_correct: true }]);
  });
});
