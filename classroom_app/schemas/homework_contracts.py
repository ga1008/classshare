from __future__ import annotations

from typing import Any

from pydantic import Field

from .api_common import ApiFlexibleRecord, ApiSuccessResponse


class AssignmentMutationResponse(ApiSuccessResponse):
    new_assignment_id: int | str | None = None
    updated_assignment_id: int | str | None = None
    deleted_assignment_id: int | str | None = None
    assignment_status: str | None = None
    due_at: str | None = None


class AssignmentTimeStateItem(ApiFlexibleRecord):
    id: int | str
    assignment_id: int | str | None = None
    status: str | None = None
    effective_status: str | None = None


class AssignmentTimeStateResponse(ApiSuccessResponse):
    server_now: str
    assignments: list[AssignmentTimeStateItem] = Field(default_factory=list)


class AssignmentStatsItem(ApiFlexibleRecord):
    assignment_id: int | str
    title: str | None = None
    status: str | None = None


class CourseAssignmentStatsResponse(ApiSuccessResponse):
    course_id: int
    assignments: list[AssignmentStatsItem] = Field(default_factory=list)


class AssignmentDraftResponse(ApiFlexibleRecord):
    exists: bool
    answers_json: str = ""
    current_page: int = 0
    client_updated_at: str = ""
    server_updated_at: str = ""
    server_version: int = 0
    files: list[dict[str, Any]] = Field(default_factory=list)
    files_by_question: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class AssignmentDraftSaveResponse(AssignmentDraftResponse):
    saved: bool | None = None


class AssignmentSubmissionsResponse(ApiSuccessResponse):
    stats: dict[str, Any]
    submissions: list[dict[str, Any]] = Field(default_factory=list)
    assignment: dict[str, Any]


class SubmissionMutationResponse(ApiSuccessResponse):
    deleted_submission_id: int | None = None
    graded_submission_id: int | None = None
    submission_id: int | None = None
    queued_for_ai: bool | None = None


class ExamPapersResponse(ApiSuccessResponse):
    papers: list[dict[str, Any]] = Field(default_factory=list)


class ExamPaperDetailResponse(ApiSuccessResponse):
    paper: dict[str, Any]
