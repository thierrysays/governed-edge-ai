"""Pydantic models for dashboard API request/response payloads."""

from pydantic import BaseModel, Field


class SessionOut(BaseModel):
    session_id: str
    started_at: str
    ended_at: str | None
    board_serial: str | None
    notes: str | None


class EventOut(BaseModel):
    id: int
    ts: str
    session_id: str
    actor: str
    detection_type: str
    detection_label: str
    confidence: float
    command: str
    command_sent: bool
    stm32_ack: bool | None
    flag: bool
    notes: str | None


class FlagRequest(BaseModel):
    notes: str | None = Field(default=None, description="Optional reviewer annotation")


class FlagResponse(BaseModel):
    event_id: int
    flagged: bool


class QueryResponse(BaseModel):
    query: str
    answer: str
    note: str
