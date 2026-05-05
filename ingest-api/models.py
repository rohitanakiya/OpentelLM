from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator
import uuid


class LLMEvent(BaseModel):
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    span_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    model: str
    provider: Literal["openai", "anthropic", "google", "cohere", "other"]
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    status: Literal["success", "error", "timeout"]
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    prompt_hash: str = Field(min_length=64, max_length=64)
    response_hash: str = Field(min_length=64, max_length=64)
    sdk_version: str = "unknown"
    deployment_version: Optional[str] = None
    tags: dict[str, str] = Field(default_factory=dict)
    # Unix ms timestamp; defaults to now if omitted
    timestamp_ms: Optional[int] = None

    @field_validator("prompt_hash", "response_hash")
    @classmethod
    def must_be_hex(cls, v: str) -> str:
        try:
            int(v, 16)
        except ValueError:
            raise ValueError("hash must be a lowercase hex string")
        return v.lower()


class IngestRequest(BaseModel):
    events: list[LLMEvent] = Field(min_length=1, max_length=500)


class IngestResponse(BaseModel):
    accepted: int
    trace_ids: list[str]
