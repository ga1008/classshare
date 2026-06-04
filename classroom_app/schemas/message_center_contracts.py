from __future__ import annotations

from typing import Any

from pydantic import Field

from .api_common import ApiFlexibleRecord, ApiSuccessResponse


class MessageCenterBootstrapResponse(ApiSuccessResponse):
    summary: dict[str, Any] | None = None
    latest_unread: dict[str, Any] | None = None


class MessageCenterSummaryResponse(ApiSuccessResponse):
    summary: dict[str, Any]
    latest_unread: dict[str, Any] | None = None


class MessageCenterItemsResponse(ApiSuccessResponse):
    items: list[dict[str, Any]] = Field(default_factory=list)


class MessageCenterMarkReadResponse(ApiSuccessResponse):
    updated_count: int
    summary: dict[str, Any]


class PrivateContactsResponse(ApiSuccessResponse):
    contacts: list[dict[str, Any]] = Field(default_factory=list)


class ClassroomPrivateContactsResponse(PrivateContactsResponse):
    class_offering_id: int | None = None


class PrivateConversationResponse(ApiSuccessResponse):
    conversation: dict[str, Any]
    summary: dict[str, Any]


class PrivateMessageSendResponse(ApiSuccessResponse):
    summary: dict[str, Any]
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    sent_message: dict[str, Any] | None = None
    ai_reply_job: dict[str, Any] | None = None


class PrivateAiReplyJobResponse(ApiSuccessResponse):
    job: dict[str, Any]


class PrivateBlocksResponse(ApiSuccessResponse):
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class PrivateBlockMutationResponse(PrivateBlocksResponse):
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any]
    block: dict[str, Any] | None = None
    removed_count: int | None = None


class MessageCenterContractFixture(ApiFlexibleRecord):
    """Flexible fixture model used by tests for nested message-center records."""

