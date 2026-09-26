from __future__ import annotations

from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(item.capitalize() for item in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class Meta(ApiModel):
    request_id: UUID


class PageMeta(Meta):
    page: int
    page_size: int
    total: int
    total_pages: int


class DataResponse(ApiModel, Generic[T]):
    data: T
    meta: Meta


class ListResponse(ApiModel, Generic[T]):
    data: list[T]
    meta: PageMeta


class ErrorBody(ApiModel):
    code: str
    message: str
    fields: dict[str, Any] = Field(default_factory=dict)
    request_id: UUID


class ErrorResponse(ApiModel):
    error: ErrorBody
