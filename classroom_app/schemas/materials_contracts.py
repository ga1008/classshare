from __future__ import annotations

from typing import Any

from pydantic import Field

from .api_common import ApiFlexibleRecord, ApiSuccessResponse


class MaterialItem(ApiFlexibleRecord):
    id: int
    name: str | None = None
    node_type: str | None = None
    material_path: str | None = None


class MaterialLibraryResponse(ApiSuccessResponse):
    current_folder: dict[str, Any] | None = None
    breadcrumbs: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    facets: dict[str, Any] = Field(default_factory=dict)
    overview: dict[str, Any] = Field(default_factory=dict)


class MaterialDetailResponse(ApiSuccessResponse):
    material: dict[str, Any]


class MaterialRepositoryResponse(ApiSuccessResponse):
    repository: dict[str, Any] | None = None


class MaterialAiGenerationCandidatesResponse(ApiSuccessResponse):
    items: list[dict[str, Any]] = Field(default_factory=list)


class MaterialAiImportActiveResponse(ApiSuccessResponse):
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    poll_interval_ms: int


class MaterialAiImportStatusResponse(ApiSuccessResponse):
    task: dict[str, Any]


class MaterialAiImportPreviewResponse(MaterialAiImportStatusResponse):
    preview: dict[str, Any]


class ClassroomMaterialsResponse(ApiSuccessResponse):
    current_folder: dict[str, Any] | None = None
    breadcrumbs: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)

